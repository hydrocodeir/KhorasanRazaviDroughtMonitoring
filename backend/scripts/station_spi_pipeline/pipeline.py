from __future__ import annotations

import json
import logging
import os
import re
from calendar import monthrange
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import geopandas as gpd
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from shapely.geometry import Point

from scripts.SDAT.core import sdat_from_vector_reference

LOGGER = logging.getLogger(__name__)
_ENV_TOKEN_RE = re.compile(r"\$\{([^}:]+)(?::-(.*?))?\}")


def _slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "_", str(value).strip().lower())
    return value.strip("_")


def _expand_env_tokens(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        default = match.group(2)
        return os.environ.get(name, default if default is not None else match.group(0))

    return _ENV_TOKEN_RE.sub(replace, value)


def _path_from_raw(value: str | Path) -> Path:
    return Path(_expand_env_tokens(str(value)))


@dataclass(frozen=True)
class StationSpiConfig:
    input_csv: Path
    output_root: Path
    dataset_key: str
    title: str
    source_key: str
    source_title: str
    boundary_key: str
    boundary_title: str
    scale: int
    chunksize: int
    compression: str
    encoding: str
    minimum_reference_years: int
    minimum_daily_coverage_ratio: float
    reference_start: str | None
    reference_end: str | None
    fallback_reference_to_available: bool
    station_id_column: str
    station_name_column: str
    lon_column: str
    lat_column: str
    elevation_column: str | None
    date_column: str
    precip_column: str

    @classmethod
    def load(cls, path: str | Path) -> "StationSpiConfig":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        dataset_key = _slug(raw["dataset_key"])
        return cls(
            input_csv=_path_from_raw(raw["input_csv"]),
            output_root=_path_from_raw(raw.get("output_root", "data/import")),
            dataset_key=dataset_key,
            title=str(raw.get("title") or dataset_key),
            source_key=_slug(raw.get("source_key") or dataset_key),
            source_title=str(raw.get("source_title") or raw.get("title") or dataset_key),
            boundary_key=_slug(raw.get("boundary_key") or "station"),
            boundary_title=str(raw.get("boundary_title") or "Stations"),
            scale=int(raw.get("scale", 3)),
            chunksize=max(10_000, int(raw.get("chunksize", 250_000))),
            compression=str(raw.get("compression", "zstd")),
            encoding=str(raw.get("encoding", "utf-8-sig")),
            minimum_reference_years=max(1, int(raw.get("minimum_reference_years", 20))),
            minimum_daily_coverage_ratio=float(raw.get("minimum_daily_coverage_ratio", 0.8)),
            reference_start=raw.get("reference_start"),
            reference_end=raw.get("reference_end"),
            fallback_reference_to_available=bool(raw.get("fallback_reference_to_available", True)),
            station_id_column=str(raw.get("station_id_column", "station_id")),
            station_name_column=str(raw.get("station_name_column", "station_name")),
            lon_column=str(raw.get("lon_column", "lon")),
            lat_column=str(raw.get("lat_column", "lat")),
            elevation_column=raw.get("elevation_column"),
            date_column=str(raw.get("date_column", "date")),
            precip_column=str(raw.get("precip_column", "rrr24")),
        )


def _monthly_period(ts: pd.Series) -> pd.Series:
    return ts.dt.to_period("M").dt.to_timestamp()


def _days_in_month(period_ts: pd.Timestamp) -> int:
    return monthrange(period_ts.year, period_ts.month)[1]


def _reference_ready(
    values: np.ndarray,
    months: pd.DatetimeIndex,
    reference_start: str | None,
    reference_end: str | None,
    minimum_reference_years: int,
) -> bool:
    valid = np.isfinite(values)
    if not np.any(valid):
        return False
    ref_mask = np.ones(len(months), dtype=bool)
    if reference_start:
        ref_mask &= months >= pd.Timestamp(reference_start).to_period("M").to_timestamp()
    if reference_end:
        ref_mask &= months <= pd.Timestamp(reference_end).to_period("M").to_timestamp()
    ref_mask &= valid
    if not np.any(ref_mask):
        return False
    counts = pd.Series(months[ref_mask].month).value_counts()
    return all(int(counts.get(month, 0)) >= int(minimum_reference_years) for month in range(1, 13))


def _compute_spi_for_station(
    monthly_precip: np.ndarray,
    months: pd.DatetimeIndex,
    config: StationSpiConfig,
) -> tuple[np.ndarray, str]:
    use_preferred = _reference_ready(
        monthly_precip,
        months,
        config.reference_start,
        config.reference_end,
        config.minimum_reference_years,
    )
    if use_preferred:
        reference_mode = "configured"
        ref_start = config.reference_start
        ref_end = config.reference_end
    elif config.fallback_reference_to_available:
        reference_mode = "fallback_available"
        ref_start = None
        ref_end = None
    else:
        reference_mode = "configured_insufficient"
        ref_start = config.reference_start
        ref_end = config.reference_end

    spi = sdat_from_vector_reference(
        monthly_precip,
        months,
        sc=config.scale,
        reference_start=ref_start,
        reference_end=ref_end,
        minimum_reference_years=config.minimum_reference_years,
    ).astype(np.float32)
    return spi, reference_mode


def _reference_label(config: StationSpiConfig, reference_mode: str) -> str:
    if reference_mode == "fallback_available":
        return "Station-specific available period"
    if config.reference_start and config.reference_end:
        return f"Configured baseline ({config.reference_start} to {config.reference_end})"
    return "Available period"


def discover_station_file(config: StationSpiConfig) -> dict[str, Any]:
    header = pd.read_csv(
        config.input_csv,
        encoding=config.encoding,
        nrows=0,
    ).columns.tolist()
    return {
        "dataset_key": config.dataset_key,
        "title": config.title,
        "input_csv": str(config.input_csv),
        "columns": header,
        "precip_column": config.precip_column,
        "date_column": config.date_column,
        "station_id_column": config.station_id_column,
    }


def _iter_station_chunks(config: StationSpiConfig):
    usecols = [
        config.station_id_column,
        config.station_name_column,
        config.lon_column,
        config.lat_column,
        config.date_column,
        config.precip_column,
    ]
    if config.elevation_column:
        usecols.append(config.elevation_column)
    yield from pd.read_csv(
        config.input_csv,
        usecols=usecols,
        chunksize=config.chunksize,
        encoding=config.encoding,
        low_memory=False,
    )


def run_station_pipeline(
    config: StationSpiConfig,
    *,
    discover_only: bool = False,
) -> list[dict[str, Any]]:
    inventory = discover_station_file(config)
    if discover_only:
        return [inventory]

    monthly_parts: list[pd.DataFrame] = []
    station_frames: list[pd.DataFrame] = []
    duplicate_rows = 0

    for chunk_index, chunk in enumerate(_iter_station_chunks(config), start=1):
        rename_map = {
            config.station_id_column: "station_id",
            config.station_name_column: "station_name",
            config.lon_column: "lon",
            config.lat_column: "lat",
            config.date_column: "date",
            config.precip_column: "precip_mm",
        }
        if config.elevation_column:
            rename_map[config.elevation_column] = "station_elevation"
        chunk = chunk.rename(columns=rename_map)
        chunk["station_id"] = chunk["station_id"].astype("string").str.strip()
        chunk["station_name"] = chunk["station_name"].astype("string").fillna(chunk["station_id"])
        chunk["date"] = pd.to_datetime(chunk["date"], errors="coerce")
        chunk["lon"] = pd.to_numeric(chunk["lon"], errors="coerce")
        chunk["lat"] = pd.to_numeric(chunk["lat"], errors="coerce")
        chunk["precip_mm"] = pd.to_numeric(chunk["precip_mm"], errors="coerce")
        if "station_elevation" in chunk.columns:
            chunk["station_elevation"] = pd.to_numeric(chunk["station_elevation"], errors="coerce")

        chunk = chunk.dropna(subset=["station_id", "date", "lon", "lat"]).copy()
        chunk = chunk[chunk["station_id"].astype(str).str.len() > 0].copy()
        if chunk.empty:
            continue

        before = len(chunk)
        chunk = chunk.sort_values(["station_id", "date"]).drop_duplicates(["station_id", "date"], keep="last")
        duplicate_rows += before - len(chunk)
        chunk["month"] = _monthly_period(chunk["date"])
        chunk["expected_days"] = chunk["month"].map(_days_in_month)

        station_agg: dict[str, Any] = {
            "station_name": ("station_name", "last"),
            "lon": ("lon", "last"),
            "lat": ("lat", "last"),
            "min_input_date": ("date", "min"),
            "max_input_date": ("date", "max"),
            "daily_rows": ("date", "size"),
            "valid_daily_precip": ("precip_mm", lambda s: int(s.notna().sum())),
        }
        if "station_elevation" in chunk.columns:
            station_agg["station_elevation"] = ("station_elevation", "last")
        station_frame = chunk.groupby("station_id", as_index=False).agg(**station_agg)
        station_frames.append(station_frame)

        monthly = chunk.groupby(["station_id", "month"], as_index=False).agg(
            monthly_precip_mm=("precip_mm", lambda s: s.sum(min_count=1)),
            valid_days=("precip_mm", lambda s: int(s.notna().sum())),
            observed_days=("date", "size"),
            expected_days=("expected_days", "max"),
        )
        monthly["coverage_ratio"] = monthly["valid_days"] / monthly["expected_days"]
        monthly_parts.append(monthly)
        LOGGER.info("Processed station chunk %s", chunk_index)

    if not monthly_parts or not station_frames:
        raise ValueError("No valid station records were found in the input CSV")

    station_info = pd.concat(station_frames, ignore_index=True)
    station_info = (
        station_info.sort_values(["station_id", "max_input_date"])
        .drop_duplicates(["station_id"], keep="last")
        .reset_index(drop=True)
    )

    monthly = pd.concat(monthly_parts, ignore_index=True)
    monthly = monthly.groupby(["station_id", "month"], as_index=False).agg(
        monthly_precip_mm=("monthly_precip_mm", lambda s: s.sum(min_count=1)),
        valid_days=("valid_days", "sum"),
        observed_days=("observed_days", "sum"),
        expected_days=("expected_days", "max"),
    )
    monthly["coverage_ratio"] = monthly["valid_days"] / monthly["expected_days"]
    monthly.loc[
        monthly["coverage_ratio"] < config.minimum_daily_coverage_ratio,
        "monthly_precip_mm",
    ] = np.nan

    min_month = monthly["month"].min()
    max_month = monthly["month"].max()
    all_months = pd.date_range(min_month, max_month, freq="MS")
    month_index = pd.DatetimeIndex(all_months)

    station_ids = station_info["station_id"].astype(str).tolist()
    spi_matrix = np.full((len(all_months), len(station_ids)), np.nan, dtype=np.float32)
    precip_matrix = np.full((len(all_months), len(station_ids)), np.nan, dtype=np.float32)
    fallback_stations = 0
    insufficient_stations = 0
    coverage_failures = int(monthly["monthly_precip_mm"].isna().sum())
    reference_modes: dict[str, str] = {}

    monthly_by_station = {
        str(station_id): frame.set_index("month").sort_index()
        for station_id, frame in monthly.groupby("station_id")
    }

    for idx, station_id in enumerate(station_ids):
        station_months = monthly_by_station.get(station_id)
        if station_months is None:
            insufficient_stations += 1
            continue
        precip = station_months.reindex(all_months)["monthly_precip_mm"].to_numpy(dtype=float)
        spi, reference_mode = _compute_spi_for_station(precip, month_index, config)
        reference_modes[station_id] = reference_mode
        if reference_mode == "fallback_available":
            fallback_stations += 1
        elif reference_mode == "configured_insufficient":
            insufficient_stations += 1
        spi_matrix[:, idx] = spi
        precip_matrix[:, idx] = precip.astype(np.float32)

    output_dir = config.output_root / config.dataset_key
    output_dir.mkdir(parents=True, exist_ok=True)

    geo_frame = station_info.copy()
    geo_frame["feature_id"] = geo_frame["station_id"].astype(str)
    geo_frame["name"] = geo_frame["station_name"].astype(str)
    geo_frame["reference_mode"] = geo_frame["feature_id"].map(lambda sid: reference_modes.get(str(sid), "configured"))
    geo_frame["uses_fallback_reference"] = geo_frame["reference_mode"].eq("fallback_available")
    geo_frame["reference_label"] = geo_frame["reference_mode"].map(lambda mode: _reference_label(config, mode))
    geo_frame["geometry"] = [Point(xy) for xy in zip(geo_frame["lon"], geo_frame["lat"])]
    geo = gpd.GeoDataFrame(geo_frame, geometry="geometry", crs="EPSG:4326")
    geo.to_parquet(output_dir / "geoinfo.parquet", compression=config.compression, index=False)

    schema = pa.schema(
        [
            ("feature_id", pa.string()),
            ("date", pa.timestamp("ms")),
            (f"spi{config.scale}", pa.float32()),
            ("precip", pa.float32()),
        ]
    )
    writer = pq.ParquetWriter(
        output_dir / "data.parquet",
        schema,
        compression=config.compression,
        use_dictionary=["feature_id"],
        write_statistics=True,
    )
    try:
        chunk_months = 24
        feature_ids = geo["feature_id"].astype(str).to_numpy()
        for start in range(0, len(all_months), chunk_months):
            end = min(len(all_months), start + chunk_months)
            count = end - start
            table = pa.Table.from_arrays(
                [
                    pa.array(np.tile(feature_ids, count), type=pa.string()),
                    pa.array(
                        np.repeat(np.asarray(all_months[start:end], dtype="datetime64[ms]"), len(feature_ids)),
                        type=pa.timestamp("ms"),
                    ),
                    pa.array(spi_matrix[start:end].reshape(-1), type=pa.float32(), from_pandas=True),
                    pa.array(precip_matrix[start:end].reshape(-1), type=pa.float32(), from_pandas=True),
                ],
                schema=schema,
            )
            writer.write_table(table)
    finally:
        writer.close()

    metadata = {
        "dataset_key": config.dataset_key,
        "title": config.title,
        "source_key": config.source_key,
        "source_title": config.source_title,
        "boundary_key": config.boundary_key,
        "boundary_title": config.boundary_title,
        "variable": config.precip_column,
        "input_units": "mm",
        "input_time_resolution": "daily",
        "aggregation_method": f"monthly sum from daily precipitation with >= {config.minimum_daily_coverage_ratio:.0%} daily coverage",
        "available_indices": [f"spi{config.scale}", "precip"],
        "reference_start": config.reference_start,
        "reference_end": config.reference_end,
        "fallback_reference_to_available": config.fallback_reference_to_available,
        "minimum_reference_years": config.minimum_reference_years,
        "feature_count": int(len(geo)),
        "min_month": str(min_month.strftime("%Y-%m")),
        "max_month": str(max_month.strftime("%Y-%m")),
        "duplicate_daily_rows_dropped": int(duplicate_rows),
        "monthly_coverage_failures": int(coverage_failures),
        "fallback_reference_station_count": int(fallback_stations),
        "configured_reference_insufficient_station_count": int(insufficient_stations),
        "input_csv": str(config.input_csv),
    }
    (output_dir / "metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    return [
        {
            "dataset_key": config.dataset_key,
            "output_dir": str(output_dir),
            "feature_count": int(len(geo)),
            "min_month": metadata["min_month"],
            "max_month": metadata["max_month"],
            "fallback_reference_station_count": int(fallback_stations),
            "configured_reference_insufficient_station_count": int(insufficient_stations),
            "duplicate_daily_rows_dropped": int(duplicate_rows),
            "monthly_coverage_failures": int(coverage_failures),
        }
    ]
