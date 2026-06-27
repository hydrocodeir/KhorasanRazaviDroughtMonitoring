from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import xarray as xr
from scipy import sparse

from scripts.SDAT.core import sdat_from_vector_reference

LOGGER = logging.getLogger("polygon-spi")
EARTH_RADIUS_M = 6_371_008.8
VECTOR_SUFFIXES = {".shp", ".gpkg", ".geojson", ".json"}
_ENV_TOKEN_RE = re.compile(r"\$\{([^}:]+)(?::-(.*?))?\}")


def _slug(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", value).strip("_").lower()
    return cleaned or "dataset"


def _expand_env_tokens(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        default = match.group(2)
        return os.environ.get(name, default if default is not None else match.group(0))

    return _ENV_TOKEN_RE.sub(replace, value)


def _path_from_raw(value: str | Path) -> Path:
    return Path(_expand_env_tokens(str(value)))


def _fingerprint(path: Path) -> str:
    stat = path.stat()
    return f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"


def _digest(parts: Iterable[str]) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(str(part).encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


@dataclass(frozen=True)
class SourceConfig:
    key: str
    title: str
    root: Path
    glob: str = "**/*.nc*"
    variable: str | None = None
    time_resolution: str = "monthly"
    units: str | None = None
    reference_start: str | None = None
    reference_end: str | None = None
    date_regex: str | None = None
    date_format: str = "%Y%m%d"
    duration_seconds: float | None = None

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "SourceConfig":
        return cls(
            key=_slug(raw["key"]),
            title=str(raw.get("title") or raw["key"]),
            root=_path_from_raw(raw["root"]),
            glob=str(raw.get("glob", "**/*.nc*")),
            variable=raw.get("variable"),
            time_resolution=str(raw.get("time_resolution", "monthly")).lower(),
            units=raw.get("units"),
            reference_start=raw.get("reference_start"),
            reference_end=raw.get("reference_end"),
            date_regex=raw.get("date_regex"),
            date_format=str(raw.get("date_format", "%Y%m%d")),
            duration_seconds=(
                float(raw["duration_seconds"])
                if raw.get("duration_seconds") is not None
                else None
            ),
        )


@dataclass(frozen=True)
class PipelineConfig:
    boundary_root: Path
    output_root: Path
    cache_root: Path
    sources: tuple[SourceConfig, ...]
    scales: tuple[int, ...] = (3,)
    minimum_reference_years: int = 20
    minimum_spatial_coverage: float = 0.8
    compression: str = "zstd"
    boundary_include: tuple[str, ...] = field(default_factory=tuple)

    @classmethod
    def load(cls, path: str | Path) -> "PipelineConfig":
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
        raw_scales = raw.get("scales")
        if raw_scales is None:
            raw_scales = [raw.get("scale", 3)]
        scales = tuple(
            sorted(
                {
                    max(1, int(value))
                    for value in raw_scales
                }
            )
        )
        return cls(
            boundary_root=_path_from_raw(raw["boundary_root"]),
            output_root=_path_from_raw(raw["output_root"]),
            cache_root=_path_from_raw(raw["cache_root"]),
            sources=tuple(SourceConfig.from_dict(item) for item in raw["sources"]),
            scales=scales or (3,),
            minimum_reference_years=int(raw.get("minimum_reference_years", 20)),
            minimum_spatial_coverage=float(raw.get("minimum_spatial_coverage", 0.8)),
            compression=str(raw.get("compression", "zstd")),
            boundary_include=tuple(_slug(v) for v in raw.get("boundary_include", [])),
        )


@dataclass(frozen=True)
class BoundaryLayer:
    key: str
    title: str
    path: Path


@dataclass(frozen=True)
class TimeSlice:
    timestamp: pd.Timestamp
    month: pd.Timestamp
    file: Path
    time_index: int | None
    time_name: str | None
    duration_seconds: float

    @property
    def fingerprint(self) -> str:
        return (
            f"{_fingerprint(self.file)}:{self.time_name}:"
            f"{self.time_index}:{self.timestamp.isoformat()}:{self.duration_seconds}"
        )


@dataclass(frozen=True)
class Grid:
    lat: np.ndarray
    lon: np.ndarray
    lat_name: str
    lon_name: str

    @property
    def shape(self) -> tuple[int, int]:
        return len(self.lat), len(self.lon)

    @property
    def extent(self) -> tuple[float, float, float, float]:
        dy = abs(float(np.median(np.diff(self.lat))))
        dx = abs(float(np.median(np.diff(self.lon))))
        return (
            float(self.lon[0] - dx / 2),
            float(self.lat[-1] - dy / 2),
            float(self.lon[-1] + dx / 2),
            float(self.lat[0] + dy / 2),
        )

    @property
    def signature(self) -> str:
        return _digest(
            [
                str(self.shape),
                repr(float(self.lat[0])),
                repr(float(self.lat[-1])),
                repr(float(self.lon[0])),
                repr(float(self.lon[-1])),
            ]
        )


def discover_boundaries(config: PipelineConfig) -> list[BoundaryLayer]:
    layers: list[BoundaryLayer] = []
    for path in sorted(config.boundary_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in VECTOR_SUFFIXES:
            continue
        relative = path.relative_to(config.boundary_root).with_suffix("")
        parts = list(relative.parts)
        if len(parts) > 1 and parts[-1].lower() == parts[-2].lower():
            parts.pop()
        key = _slug("_".join(parts))
        if config.boundary_include and key not in config.boundary_include:
            continue
        title = " / ".join(parts)
        layers.append(BoundaryLayer(key=key, title=title, path=path))
    return layers


def discover_source_files(source: SourceConfig) -> list[Path]:
    files = sorted(path for path in source.root.glob(source.glob) if path.is_file())
    if not files:
        raise FileNotFoundError(f"No precipitation files found for {source.key}: {source.root}")
    return files


def _find_coord(ds: xr.Dataset, kind: str) -> str:
    candidates = ("lat", "latitude", "y") if kind == "lat" else ("lon", "longitude", "x")
    for name in candidates:
        if name in ds.coords:
            return name
    standard = "latitude" if kind == "lat" else "longitude"
    for name, coord in ds.coords.items():
        if str(coord.attrs.get("standard_name", "")).lower() == standard:
            return str(name)
    raise ValueError(f"Could not detect {kind} coordinate")


def _find_variable(ds: xr.Dataset, configured: str | None) -> str:
    if configured:
        if configured not in ds.data_vars:
            raise KeyError(f"Variable {configured!r} not found; available={list(ds.data_vars)}")
        return configured
    scored: list[tuple[int, str]] = []
    for name, var in ds.data_vars.items():
        dims = {str(d).lower() for d in var.dims}
        if not (dims & {"lat", "latitude", "y"}) or not (dims & {"lon", "longitude", "x"}):
            continue
        attrs = " ".join(
            str(var.attrs.get(k, "")).lower()
            for k in ("standard_name", "long_name", "units")
        )
        score = 0
        if "precip" in attrs or "rain" in attrs:
            score += 10
        if "kg m-2 s-1" in attrs or "mm" in attrs:
            score += 3
        scored.append((score, str(name)))
    if not scored:
        raise ValueError("Could not infer a precipitation variable")
    return max(scored)[1]


def _find_time_coord(ds: xr.Dataset, variable: str, lat_name: str, lon_name: str) -> str:
    for name in ("time", "date", "datetime"):
        if name in ds[variable].dims and name in ds.coords:
            return name
    for name in ds[variable].dims:
        coord = ds.coords.get(name)
        if coord is None:
            continue
        if str(coord.attrs.get("standard_name", "")).lower() == "time":
            return str(name)
        if str(coord.attrs.get("axis", "")).upper() == "T":
            return str(name)
    remaining = [
        str(name)
        for name in ds[variable].dims
        if name not in {lat_name, lon_name} and name in ds.coords
    ]
    if len(remaining) == 1:
        return remaining[0]
    raise ValueError("Could not detect the precipitation time coordinate")


def inspect_grid(source: SourceConfig, first_file: Path) -> tuple[Grid, str, str]:
    with xr.open_dataset(first_file, decode_times=True, mask_and_scale=True) as ds:
        lat_name = _find_coord(ds, "lat")
        lon_name = _find_coord(ds, "lon")
        variable = _find_variable(ds, source.variable)
        lat = np.asarray(ds[lat_name].values, dtype=float)
        lon = np.asarray(ds[lon_name].values, dtype=float)
        units = str(source.units or ds[variable].attrs.get("units", "")).strip()
    if lat.ndim != 1 or lon.ndim != 1:
        raise ValueError("Only regular 1D latitude/longitude grids are supported")
    if lat[0] < lat[-1]:
        lat = lat[::-1]
    if lon[0] > lon[-1]:
        lon = lon[::-1]
    return Grid(lat=lat, lon=lon, lat_name=lat_name, lon_name=lon_name), variable, units


def build_time_slices(source: SourceConfig, files: list[Path], variable: str) -> list[TimeSlice]:
    slices: list[TimeSlice] = []
    filename_pattern = re.compile(source.date_regex) if source.date_regex else None
    for path in files:
        if filename_pattern:
            match = filename_pattern.search(path.name)
            if not match:
                LOGGER.warning("Skipping file whose date does not match: %s", path)
                continue
            timestamp = pd.Timestamp(datetime.strptime(match.group(1), source.date_format))
            slices.append(
                TimeSlice(
                    timestamp=timestamp,
                    month=timestamp.to_period("M").to_timestamp(),
                    file=path,
                    time_index=None,
                    time_name=None,
                    duration_seconds=float(source.duration_seconds or 86400),
                )
            )
            continue

        with xr.open_dataset(path, decode_times=True, mask_and_scale=False) as ds:
            lat_name = _find_coord(ds, "lat")
            lon_name = _find_coord(ds, "lon")
            time_name = _find_time_coord(ds, variable, lat_name, lon_name)
            times = pd.DatetimeIndex(pd.to_datetime(ds[time_name].values))
            bounds = None
            bounds_name = ds[time_name].attrs.get("bounds")
            if bounds_name and bounds_name in ds:
                bounds = pd.to_datetime(ds[bounds_name].values)
            for index, timestamp in enumerate(times):
                duration = source.duration_seconds
                if duration is None and bounds is not None:
                    duration = (bounds[index, 1] - bounds[index, 0]).total_seconds()
                if duration is None:
                    duration = 86400 if source.time_resolution == "daily" else 1
                slices.append(
                    TimeSlice(
                        timestamp=timestamp,
                        month=timestamp.to_period("M").to_timestamp(),
                        file=path,
                        time_index=index,
                        time_name=time_name,
                        duration_seconds=float(duration),
                    )
                )
    slices = sorted(
        slices,
        key=lambda item: (item.timestamp, str(item.file), item.time_index or -1),
    )
    if source.time_resolution == "daily" and slices:
        grouped: dict[pd.Timestamp, list[TimeSlice]] = defaultdict(list)
        for item in slices:
            grouped[item.month].append(item)
        latest = max(grouped)
        incomplete: list[pd.Timestamp] = []
        for month, items in grouped.items():
            unique_days = {
                item.timestamp.normalize()
                for item in items
            }
            if len(items) != len(unique_days):
                raise ValueError(
                    f"Duplicate daily precipitation timestep(s) in {month.strftime('%Y-%m')}"
                )
            if len(unique_days) != month.days_in_month:
                incomplete.append(month)
        if incomplete:
            preview = ", ".join(month.strftime("%Y-%m") for month in incomplete[:6])
            LOGGER.warning(
                "Ignoring incomplete daily precipitation month(s): %s",
                preview,
            )
            incomplete_set = set(incomplete)
            slices = [item for item in slices if item.month not in incomplete_set]
    return slices


_ARABIC_CHAR_RE = re.compile(r"[\u0600-\u06FF]")
_MOJIBAKE_HINT_RE = re.compile(r"[\u00C0-\u00FF\u0080-\u009F]")
_PERSIAN_TRANSLATION = str.maketrans({"ي": "ی", "ك": "ک"})


def _normalize_persian_text(value: str) -> str:
    return value.translate(_PERSIAN_TRANSLATION)


def _repair_mojibake_text(value: object) -> object:
    """Repair common Persian DBF mojibake: CP1256 bytes decoded as Latin-1."""

    if not isinstance(value, str) or not value:
        return value
    if _ARABIC_CHAR_RE.search(value) or not _MOJIBAKE_HINT_RE.search(value):
        return _normalize_persian_text(value)
    try:
        repaired = value.encode("latin1").decode("cp1256")
    except UnicodeError:
        return value
    return _normalize_persian_text(repaired) if _ARABIC_CHAR_RE.search(repaired) else _normalize_persian_text(value)


def _repair_text_columns(frame):
    text_columns = [
        column
        for column in frame.columns
        if column != frame.geometry.name and str(frame[column].dtype) in {"object", "string"}
    ]
    for column in text_columns:
        frame[column] = frame[column].map(_repair_mojibake_text)
    return frame


def _load_boundary(layer: BoundaryLayer):
    try:
        import geopandas as gpd
    except ImportError as exc:
        raise RuntimeError("geopandas is required to read boundary files") from exc

    frame = gpd.read_file(layer.path)
    if frame.empty:
        raise ValueError(f"Boundary layer is empty: {layer.path}")
    if frame.crs is None:
        raise ValueError(f"Boundary layer has no CRS: {layer.path}")
    frame = frame.to_crs(4326)
    frame = frame[frame.geometry.notna() & ~frame.geometry.is_empty].copy()
    frame.geometry = frame.geometry.make_valid()
    frame = _repair_text_columns(frame)

    lower = {str(column).lower(): str(column) for column in frame.columns}
    id_column = next(
        (lower[key] for key in ("shapeid", "feature_id", "gid", "id", "code", "shapegroup") if key in lower),
        None,
    )
    name_column = next(
        (
            lower[key]
            for key in (
                "mah_name",
                "shapename",
                "shape_name",
                "name",
                "title",
            )
            if key in lower
        ),
        None,
    )
    if id_column is None:
        frame["feature_id"] = [f"{layer.key}_{i + 1}" for i in range(len(frame))]
    else:
        frame["feature_id"] = frame[id_column].astype(str)
    if name_column is None:
        frame["name"] = frame["feature_id"]
    else:
        frame["name"] = frame[name_column].fillna(frame["feature_id"]).astype(str)

    duplicates = frame["feature_id"].duplicated(keep=False)
    if duplicates.any():
        frame.loc[duplicates, "feature_id"] = [
            f"{value}_{index + 1}"
            for index, value in enumerate(frame.loc[duplicates, "feature_id"].tolist())
        ]
    return frame.reset_index(drop=True)


def _write_geoparquet(frame, path: Path, compression: str) -> None:
    properties = [c for c in frame.columns if c != frame.geometry.name]
    keep = ["feature_id", "name"] + [
        c for c in properties if c not in {"feature_id", "name"}
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    frame[keep + [frame.geometry.name]].to_parquet(
        path,
        compression=compression,
        index=False,
    )


def _boundary_fingerprint(layer: BoundaryLayer) -> str:
    if layer.path.suffix.lower() == ".shp":
        parts = []
        for suffix in (".shp", ".shx", ".dbf", ".prj", ".cpg"):
            sibling = layer.path.with_suffix(suffix)
            if sibling.exists():
                parts.append(_fingerprint(sibling))
        return _digest(parts)
    return _digest([_fingerprint(layer.path)])


def _cell_area_by_row(grid: Grid) -> np.ndarray:
    lat = np.deg2rad(grid.lat)
    dlat = abs(float(np.median(np.diff(lat))))
    dlon = abs(float(np.deg2rad(np.median(np.diff(grid.lon)))))
    north = np.minimum(np.pi / 2, lat + dlat / 2)
    south = np.maximum(-np.pi / 2, lat - dlat / 2)
    return EARTH_RADIUS_M**2 * dlon * np.abs(np.sin(north) - np.sin(south))


def load_or_build_weights(
    *,
    config: PipelineConfig,
    source: SourceConfig,
    layer: BoundaryLayer,
    grid: Grid,
    frame,
) -> tuple[sparse.csr_matrix, np.ndarray]:
    cache_dir = config.cache_root / "weights"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_key = _digest([grid.signature, _boundary_fingerprint(layer), "spherical-area-v1"])
    cache_path = cache_dir / f"{source.key}__{layer.key}__{cache_key[:16]}.npz"
    coverage_path = cache_path.with_suffix(".coverage.npy")
    if cache_path.exists() and coverage_path.exists():
        matrix = sparse.load_npz(cache_path).tocsr()
        coverage = np.load(coverage_path)
        if (
            matrix.shape == (len(frame), grid.shape[0] * grid.shape[1])
            and coverage.shape == (len(frame),)
        ):
            LOGGER.info("Using cached polygon/grid weights: %s", cache_path)
            return matrix, coverage

    try:
        from exactextract import exact_extract
        from exactextract.raster import NumPyRasterSource
    except ImportError as exc:
        raise RuntimeError("exactextract is required to build polygon/grid weights") from exc

    LOGGER.info("Building exact intersection weights for %s × %s", source.key, layer.key)
    xmin, ymin, xmax, ymax = grid.extent
    template = np.zeros(grid.shape, dtype=np.uint8)
    raster = NumPyRasterSource(
        template,
        xmin=xmin,
        ymin=ymin,
        xmax=xmax,
        ymax=ymax,
        nodata=None,
        srs_wkt="EPSG:4326",
    )
    extracted = exact_extract(
        raster,
        frame[["feature_id", frame.geometry.name]],
        ["cell_id", "coverage"],
        include_cols=["feature_id"],
        output="pandas",
        strategy="raster-sequential",
        max_cells_in_memory=10_000_000,
    )

    rows: list[np.ndarray] = []
    columns: list[np.ndarray] = []
    weights: list[np.ndarray] = []
    row_areas = _cell_area_by_row(grid)
    width = grid.shape[1]
    for row_index, record in extracted.reset_index(drop=True).iterrows():
        cell_ids = np.asarray(record["cell_id"], dtype=np.int64)
        coverage = np.asarray(record["coverage"], dtype=np.float64)
        if not len(cell_ids):
            continue
        cell_rows = cell_ids // width
        area_weights = coverage * row_areas[cell_rows]
        rows.append(np.full(len(cell_ids), row_index, dtype=np.int32))
        columns.append(cell_ids)
        weights.append(area_weights)

    if not rows:
        raise ValueError(f"No boundary polygons overlap the precipitation grid: {layer.path}")
    matrix = sparse.csr_matrix(
        (
            np.concatenate(weights),
            (np.concatenate(rows), np.concatenate(columns)),
        ),
        shape=(len(frame), grid.shape[0] * grid.shape[1]),
        dtype=np.float64,
    )
    intersection_areas = np.asarray(matrix.sum(axis=1)).reshape(-1)
    try:
        from pyproj import Geod
    except ImportError as exc:
        raise RuntimeError("pyproj is required to calculate polygon coverage") from exc
    geod = Geod(ellps="WGS84")
    polygon_areas = np.asarray(
        [abs(geod.geometry_area_perimeter(geometry)[0]) for geometry in frame.geometry],
        dtype=np.float64,
    )
    coverage = np.divide(
        intersection_areas,
        polygon_areas,
        out=np.zeros_like(intersection_areas),
        where=polygon_areas > 0,
    )
    coverage = np.clip(coverage, 0.0, 1.0).astype(np.float32)

    row_sums = intersection_areas
    inverse = np.zeros_like(row_sums)
    inverse[row_sums > 0] = 1.0 / row_sums[row_sums > 0]
    matrix = sparse.diags(inverse) @ matrix
    matrix = matrix.astype(np.float32)
    sparse.save_npz(cache_path, matrix, compressed=True)
    np.save(coverage_path, coverage)
    return matrix, coverage


def _normalize_array(array: xr.DataArray, grid: Grid) -> np.ndarray:
    array = array.squeeze(drop=True).transpose(grid.lat_name, grid.lon_name)
    lat = np.asarray(array[grid.lat_name].values, dtype=float)
    lon = np.asarray(array[grid.lon_name].values, dtype=float)
    values = np.asarray(array.values, dtype=np.float64)
    if lat[0] < lat[-1]:
        values = values[::-1, :]
        lat = lat[::-1]
    if lon[0] > lon[-1]:
        values = values[:, ::-1]
        lon = lon[::-1]
    if values.shape != grid.shape or not np.allclose(lat, grid.lat) or not np.allclose(lon, grid.lon):
        raise ValueError("A precipitation file uses a grid that differs from the source grid")
    return values


def _to_mm(values: np.ndarray, units: str, duration_seconds: float) -> np.ndarray:
    normalized = re.sub(r"\s+", " ", units.strip().lower()).replace("**", "^")
    if normalized in {"mm", "millimeter", "millimeters", "kg m-2", "kg/m2"}:
        return values
    daily_depth_units = {
        "mm d-1",
        "mm d^-1",
        "mm/day",
        "mm per day",
    }
    if normalized in daily_depth_units:
        return values * (duration_seconds / 86400.0)
    rate_units = {
        "kg m-2 s-1",
        "kg m^-2 s^-1",
        "kg/m2/s",
        "mm s-1",
        "mm/s",
    }
    if normalized in rate_units:
        return values * duration_seconds
    raise ValueError(f"Unsupported precipitation units: {units!r}")


def _zonal_mean(matrix: sparse.csr_matrix, values: np.ndarray) -> np.ndarray:
    flat = values.reshape(-1)
    valid = np.isfinite(flat)
    numerator = matrix @ np.where(valid, flat, 0.0)
    denominator = matrix @ valid.astype(np.float32)
    return np.divide(
        numerator,
        denominator,
        out=np.full(matrix.shape[0], np.nan, dtype=np.float64),
        where=denominator > 0,
    )


def generate_monthly_for_layers(
    *,
    config: PipelineConfig,
    source: SourceConfig,
    variable: str,
    units: str,
    grid: Grid,
    slices: list[TimeSlice],
    contexts: list[dict[str, Any]],
) -> tuple[list[pd.Timestamp], dict[str, np.ndarray]]:
    """Read each source month once and update every selected boundary layer."""
    grouped: dict[pd.Timestamp, list[TimeSlice]] = defaultdict(list)
    for item in slices:
        grouped[item.month].append(item)
    if not grouped:
        raise ValueError(f"No complete precipitation months found for {source.key}")
    observed_months = sorted(grouped)
    all_months = list(pd.date_range(observed_months[0], observed_months[-1], freq="MS"))
    outputs = {
        context["layer"].key: np.full(
            (len(all_months), context["weights"].shape[0]),
            np.nan,
            dtype=np.float32,
        )
        for context in contexts
    }

    for month_index, month in enumerate(all_months):
        items = grouped.get(month, [])
        month_name = month.strftime("%Y-%m")
        stale: list[tuple[dict[str, Any], Path, Path, str]] = []

        for context in contexts:
            layer: BoundaryLayer = context["layer"]
            month_root = config.cache_root / "monthly" / source.key / layer.key
            month_root.mkdir(parents=True, exist_ok=True)
            data_path = month_root / f"{month_name}.npy"
            manifest_path = month_root / f"{month_name}.json"
            fingerprint = _digest(
                [
                    grid.signature,
                    _boundary_fingerprint(layer),
                    str(config.minimum_spatial_coverage),
                    *[item.fingerprint for item in items],
                    variable,
                    units,
                ]
            )
            if data_path.exists() and manifest_path.exists():
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if manifest.get("fingerprint") == fingerprint:
                    cached = np.load(data_path)
                    if cached.shape == (context["weights"].shape[0],):
                        outputs[layer.key][month_index] = cached
                        continue
            stale.append((context, data_path, manifest_path, fingerprint))

        if not stale:
            continue

        if not items:
            for context, data_path, manifest_path, fingerprint in stale:
                layer = context["layer"]
                zonal = np.full(context["weights"].shape[0], np.nan, dtype=np.float32)
                np.save(data_path, zonal)
                manifest_path.write_text(
                    json.dumps(
                        {
                            "fingerprint": fingerprint,
                            "month": month_name,
                            "source_files": [],
                            "status": "missing-or-incomplete",
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                outputs[layer.key][month_index] = zonal
            continue

        LOGGER.info(
            "Aggregating %s %s for %d boundary layer(s)",
            source.key,
            month_name,
            len(stale),
        )
        monthly_grid = np.zeros(grid.shape, dtype=np.float64)
        valid_any = np.zeros(grid.shape, dtype=bool)
        by_file: dict[Path, list[TimeSlice]] = defaultdict(list)
        for item in items:
            by_file[item.file].append(item)
        for path, file_items in by_file.items():
            with xr.open_dataset(path, decode_times=True, mask_and_scale=True) as ds:
                for item in file_items:
                    array = ds[variable]
                    if item.time_index is not None:
                        array = array.isel({str(item.time_name): item.time_index})
                    values = _to_mm(
                        _normalize_array(array, grid),
                        units=units,
                        duration_seconds=item.duration_seconds,
                    )
                    finite = np.isfinite(values)
                    monthly_grid[finite] += values[finite]
                    valid_any |= finite
        monthly_grid[~valid_any] = np.nan

        for context, data_path, manifest_path, fingerprint in stale:
            layer = context["layer"]
            zonal = _zonal_mean(context["weights"], monthly_grid).astype(np.float32)
            zonal[
                context["spatial_coverage"] < config.minimum_spatial_coverage
            ] = np.nan
            np.save(data_path, zonal)
            manifest_path.write_text(
                json.dumps(
                    {
                        "fingerprint": fingerprint,
                        "month": month_name,
                        "source_files": [str(item.file) for item in items],
                    },
                    indent=2,
                ),
                encoding="utf-8",
            )
            outputs[layer.key][month_index] = zonal

    return all_months, outputs


def compute_spi(
    precipitation: np.ndarray,
    months: list[pd.Timestamp],
    source: SourceConfig,
    scale: int,
    minimum_reference_years: int,
) -> np.ndarray:
    spi = np.full_like(precipitation, np.nan, dtype=np.float32)
    month_index = pd.DatetimeIndex(months)
    for feature_index in range(precipitation.shape[1]):
        spi[:, feature_index] = sdat_from_vector_reference(
            precipitation[:, feature_index],
            month_index,
            sc=scale,
            reference_start=source.reference_start,
            reference_end=source.reference_end,
            minimum_reference_years=minimum_reference_years,
        ).astype(np.float32)
    return spi


def _write_parquet(
    path: Path,
    feature_ids: np.ndarray,
    months: list[pd.Timestamp],
    spi: np.ndarray,
    scale: int,
    compression: str,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    schema = pa.schema(
        [
            ("feature_id", pa.string()),
            ("date", pa.timestamp("ms")),
            (f"spi{scale}", pa.float32()),
        ]
    )
    writer = pq.ParquetWriter(
        path,
        schema,
        compression=compression,
        use_dictionary=["feature_id"],
        write_statistics=True,
    )
    try:
        chunk = 12
        for start in range(0, len(months), chunk):
            end = min(len(months), start + chunk)
            count = end - start
            table = pa.Table.from_arrays(
                [
                    pa.array(np.tile(feature_ids, count), type=pa.string()),
                    pa.array(
                        np.repeat(np.asarray(months[start:end], dtype="datetime64[ms]"), len(feature_ids)),
                        type=pa.timestamp("ms"),
                    ),
                    pa.array(spi[start:end].reshape(-1), type=pa.float32(), from_pandas=True),
                ],
                schema=schema,
            )
            writer.write_table(table)
    finally:
        writer.close()


def run_pipeline(
    config: PipelineConfig,
    *,
    source_keys: set[str] | None = None,
    boundary_keys: set[str] | None = None,
    scales: set[int] | None = None,
    discover_only: bool = False,
) -> list[dict[str, Any]]:
    boundaries = discover_boundaries(config)
    if boundary_keys:
        boundaries = [item for item in boundaries if item.key in boundary_keys]
    sources = [
        source for source in config.sources
        if not source_keys or source.key in source_keys
    ]
    if not boundaries:
        raise ValueError("No boundary layers matched the requested selection")
    if not sources:
        raise ValueError("No precipitation sources matched the requested selection")
    inventory = {
        "sources": [
            {
                "key": source.key,
                "title": source.title,
                "root": str(source.root),
                "files": len(discover_source_files(source)),
            }
            for source in sources
        ],
        "boundaries": [
            {"key": layer.key, "title": layer.title, "path": str(layer.path)}
            for layer in boundaries
        ],
        "scales": list(sorted(scales or set(config.scales))),
    }
    if discover_only:
        return [inventory]

    results: list[dict[str, Any]] = []
    for source in sources:
        files = discover_source_files(source)
        grid, variable, units = inspect_grid(source, files[0])
        slices = build_time_slices(source, files, variable)
        contexts: list[dict[str, Any]] = []
        for layer in boundaries:
            frame = _load_boundary(layer)
            source_feature_count = len(frame)
            weights, spatial_coverage = load_or_build_weights(
                config=config,
                source=source,
                layer=layer,
                grid=grid,
                frame=frame,
            )
            eligible = spatial_coverage >= config.minimum_spatial_coverage
            if not np.any(eligible):
                LOGGER.warning(
                    "Skipping %s × %s: no polygon meets %.0f%% spatial coverage",
                    source.key,
                    layer.key,
                    config.minimum_spatial_coverage * 100,
                )
                continue
            frame = frame.loc[eligible].reset_index(drop=True)
            weights = weights[eligible].tocsr()
            spatial_coverage = spatial_coverage[eligible]
            frame["spatial_coverage"] = spatial_coverage
            contexts.append(
                {
                    "layer": layer,
                    "frame": frame,
                    "weights": weights,
                    "spatial_coverage": spatial_coverage,
                    "source_feature_count": source_feature_count,
                }
            )

        months, precipitation_by_layer = generate_monthly_for_layers(
            config=config,
            source=source,
            variable=variable,
            units=units,
            grid=grid,
            slices=slices,
            contexts=contexts,
        )
        for context in contexts:
            layer = context["layer"]
            frame = context["frame"]
            precipitation = precipitation_by_layer[layer.key]
            incomplete_months = [
                month.strftime("%Y-%m")
                for month, values in zip(months, precipitation)
                if not np.any(np.isfinite(values))
            ]
            selected_scales = tuple(
                scale for scale in config.scales
                if scales is None or scale in scales
            )
            for scale in selected_scales:
                spi = compute_spi(
                    precipitation,
                    months,
                    source,
                    scale,
                    config.minimum_reference_years,
                )
                dataset_key = _slug(f"{source.key}_{layer.key}_spi{scale}")
                output_dir = config.output_root / dataset_key
                output_dir.mkdir(parents=True, exist_ok=True)
                _write_geoparquet(
                    frame,
                    output_dir / "geoinfo.parquet",
                    config.compression,
                )
                _write_parquet(
                    output_dir / "data.parquet",
                    frame["feature_id"].astype(str).to_numpy(),
                    months,
                    spi,
                    scale,
                    config.compression,
                )
                metadata = {
                    "dataset_key": dataset_key,
                    "title": f"{source.title} SPI-{scale} — {layer.title}",
                    "source_key": source.key,
                    "source_title": source.title,
                    "boundary_key": layer.key,
                    "boundary_title": layer.title,
                    "scale": scale,
                    "available_indices": [f"spi{scale}"],
                    "variable": variable,
                    "input_units": units,
                    "spatial_method": "exact polygon-cell intersection; spherical-area weighted mean precipitation",
                    "minimum_spatial_coverage": config.minimum_spatial_coverage,
                    "grid_extent": list(grid.extent),
                    "reference_start": source.reference_start,
                    "reference_end": source.reference_end,
                    "incomplete_months": incomplete_months,
                    "min_month": months[0].strftime("%Y-%m"),
                    "max_month": months[-1].strftime("%Y-%m"),
                    "feature_count": len(frame),
                    "excluded_feature_count": context["source_feature_count"] - len(frame),
                }
                (output_dir / "metadata.json").write_text(
                    json.dumps(metadata, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                results.append(metadata)
                LOGGER.info("Wrote dashboard dataset: %s", output_dir)
    return results
