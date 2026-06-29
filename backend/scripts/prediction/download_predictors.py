"""Prepare monthly predictors for drought prediction from local files.

Outputs one compact file per source:

    data/prediction/features/<source_key>/monthly_predictors.parquet

The training script joins these predictors by month. If a source is not yet
configured, the training pipeline still works from the drought-index history
alone, but these files enable the full multivariate LSTM+attention method.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import pandas as pd
import xarray as xr


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_ROOT = ROOT / "data" / "prediction" / "features"
DEFAULT_BBOX = (56.0, 33.0, 62.5, 38.5)  # lon_min, lat_min, lon_max, lat_max
ENSO_URL = "https://psl.noaa.gov/data/correlation/nina34.data"


def log_progress(message: str) -> None:
    print(message, flush=True)


def month_start(values: pd.Series) -> pd.Series:
    return pd.to_datetime(values).dt.to_period("M").dt.to_timestamp()


def area_weighted_mean(da: xr.DataArray, bbox: tuple[float, float, float, float]) -> xr.DataArray:
    lon_min, lat_min, lon_max, lat_max = bbox
    lon_name = "lon" if "lon" in da.coords else "longitude"
    lat_name = "lat" if "lat" in da.coords else "latitude"
    subset = da.sel({lon_name: slice(lon_min, lon_max), lat_name: slice(lat_max, lat_min)})
    if subset.sizes.get(lat_name, 0) == 0:
        subset = da.sel({lon_name: slice(lon_min, lon_max), lat_name: slice(lat_min, lat_max)})
    weights = np.cos(np.deg2rad(subset[lat_name]))
    return subset.weighted(weights).mean(dim=[lat_name, lon_name], skipna=True)


def monthly_anomaly(series: pd.Series, dates: pd.Series, baseline_start: str, baseline_end: str) -> pd.Series:
    work = pd.DataFrame({"date": pd.to_datetime(dates), "value": pd.to_numeric(series, errors="coerce")})
    work["month"] = work["date"].dt.month
    baseline = work[(work["date"] >= baseline_start) & (work["date"] <= baseline_end)]
    clim = baseline.groupby("month")["value"].mean()
    return work.apply(lambda row: row["value"] - clim.get(row["month"], 0.0), axis=1)


def fetch_enso() -> pd.DataFrame:
    """Read NOAA PSL Nino 3.4 monthly index text table."""

    with urlopen(ENSO_URL, timeout=60) as response:
        text = response.read().decode("utf-8", errors="replace")
    rows = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) != 13 or not parts[0].isdigit():
            continue
        year = int(parts[0])
        for month, raw in enumerate(parts[1:], start=1):
            try:
                value = float(raw)
            except ValueError:
                continue
            if value < -90:
                continue
            rows.append({"date": pd.Timestamp(year=year, month=month, day=1), "enso_nino34": value})
    return pd.DataFrame(rows)


def load_enso(enso_file: Path | None) -> pd.DataFrame:
    if enso_file is not None:
        suffix = enso_file.suffix.lower()
        if suffix in {".parquet", ".pq"}:
            frame = pd.read_parquet(enso_file)
        else:
            frame = pd.read_csv(enso_file)
        if "date" not in frame.columns or "enso_nino34" not in frame.columns:
            raise ValueError("ENSO file must contain 'date' and 'enso_nino34' columns.")
        frame = frame.copy()
        frame["date"] = month_start(frame["date"])
        return frame[["date", "enso_nino34"]].dropna(subset=["date"]).sort_values("date")
    return fetch_enso()


def open_netcdf_collection(files: list[Path]) -> xr.Dataset:
    """Open one or more NetCDF files without requiring dask.

    ``xarray.open_mfdataset`` expects a chunk manager such as dask. In this
    project we want predictor preparation to work even in lean environments,
    so we open files eagerly and combine them ourselves.
    """

    ordered = [Path(path) for path in files]
    if not ordered:
        raise RuntimeError("No NetCDF files were provided.")
    datasets = [xr.open_dataset(path) for path in ordered]
    if len(datasets) == 1:
        return datasets[0]
    try:
        return xr.combine_by_coords(datasets, combine_attrs="override")
    except Exception:
        return xr.combine_nested(datasets, concat_dim="time", combine_attrs="override")


def dataset_series_from_files(
    *,
    files: list[Path],
    variable: str | None,
    output_name: str,
    bbox: tuple[float, float, float, float],
    progress_label: str | None = None,
    progress_start: int = 0,
    progress_total: int | None = None,
) -> pd.DataFrame:
    """Reduce NetCDF inputs to a monthly series with low memory overhead.

    Instead of opening a full multi-file dataset into memory, this function
    processes each file independently, extracts the area-weighted mean, and
    concatenates only the compact time series.
    """

    if not files:
        raise RuntimeError("No NetCDF files were provided.")

    parts: list[pd.DataFrame] = []
    ordered = sorted(files)
    total = int(progress_total or len(ordered) or 1)
    for idx, path in enumerate(ordered, start=1):
        if progress_label:
            absolute_step = progress_start + idx
            pct = (100.0 * absolute_step) / total
            log_progress(
                f"[progress] {progress_label}: file {idx}/{len(ordered)} "
                f"({path.name}) | overall {absolute_step}/{total} = {pct:0.1f}%"
            )
        ds = xr.open_dataset(path)
        data_var = variable or (output_name if output_name in ds.data_vars else next(iter(ds.data_vars)))
        if data_var not in ds:
            available = ", ".join(repr(name) for name in ds.data_vars) or "<none>"
            raise ValueError(
                f"Variable {data_var!r} not found in {path.name!r}. Available variables: {available}."
            )
        series = area_weighted_mean(ds[data_var], bbox).to_dataframe(name=output_name).reset_index()
        time_col = "time" if "time" in series.columns else "date"
        series["date"] = month_start(series[time_col])
        parts.append(series[["date", output_name]])
        try:
            ds.close()
        except Exception:
            pass

    frame = pd.concat(parts, ignore_index=True)
    frame = frame.groupby("date", as_index=False)[output_name].mean()
    if progress_label:
        log_progress(
            f"[done] {progress_label}: monthly rows={len(frame):,}, "
            f"min={frame['date'].min().strftime('%Y-%m') if not frame.empty else 'n/a'}, "
            f"max={frame['date'].max().strftime('%Y-%m') if not frame.empty else 'n/a'}"
        )
    return frame.sort_values("date")


def prepare_terraclimate(
    *,
    input_files: list[Path],
    output_root: Path,
    bbox: tuple[float, float, float, float],
    baseline_start: str,
    baseline_end: str,
    enso: pd.DataFrame,
) -> Path:
    variables = {
        "ppt": "precip_mm",
        "tmin": "tmin_c",
        "tmax": "tmax_c",
        "soil": "soil_moisture",
        "pet": "pet_mm",
    }
    if not input_files:
        raise RuntimeError("No local TerraClimate NetCDF files were provided.")
    frame: pd.DataFrame | None = None
    for var, out_col in variables.items():
        matches = [path for path in input_files if f"_{var}_" in path.name.lower()]
        if not matches:
            raise RuntimeError(
                f"Missing TerraClimate files for variable {var!r}. Expected files like TerraClimate_{var}_YYYY.nc."
            )
        print(f"[terraclimate] preparing {var} from {len(matches)} file(s)")
        series = dataset_series_from_files(
            files=sorted(matches),
            variable=var,
            output_name=out_col,
            bbox=bbox,
        )
        frame = series if frame is None else frame.merge(series, on="date", how="outer")
    if frame is None:
        raise RuntimeError("No TerraClimate predictors were prepared.")

    frame = frame.sort_values("date")
    frame["tmean_c"] = (frame["tmin_c"] + frame["tmax_c"]) / 2.0
    frame["precip_anom"] = monthly_anomaly(frame["precip_mm"], frame["date"], baseline_start, baseline_end)
    frame["tmean_anom"] = monthly_anomaly(frame["tmean_c"], frame["date"], baseline_start, baseline_end)
    frame["soil_moisture_anom"] = monthly_anomaly(frame["soil_moisture"], frame["date"], baseline_start, baseline_end)
    frame["pet_anom"] = monthly_anomaly(frame["pet_mm"], frame["date"], baseline_start, baseline_end)
    frame = frame.merge(enso, on="date", how="left")
    frame["source_key"] = "terraclimate"

    cols = [
        "date",
        "source_key",
        "precip_anom",
        "tmean_anom",
        "soil_moisture_anom",
        "pet_anom",
        "enso_nino34",
    ]
    out_dir = output_root / "terraclimate"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "monthly_predictors.parquet"
    frame[cols].to_parquet(out_path, index=False)
    print(f"[terraclimate] wrote {out_path}")
    return out_path


def prepare_from_netcdf(
    *,
    source_key: str,
    input_files: list[Path],
    output_root: Path,
    bbox: tuple[float, float, float, float],
    variable_map: dict[str, str],
    baseline_start: str,
    baseline_end: str,
    enso: pd.DataFrame,
) -> Path:
    """Prepare predictors from local NetCDF files.

    ``variable_map`` maps NetCDF variable names to output columns, e.g.
    ``Rainf_tavg=precip`` or ``Tair_f_tavg=tmean``.
    """

    if not input_files:
        raise RuntimeError(f"No NetCDF files passed for {source_key}.")
    frame: pd.DataFrame | None = None
    for nc_var, out_name in variable_map.items():
        series = dataset_series_from_files(
            files=input_files,
            variable=nc_var,
            output_name=out_name,
            bbox=bbox,
        )
        frame = series if frame is None else frame.merge(series, on="date", how="outer")
    if frame is None:
        raise RuntimeError(f"No variables prepared for {source_key}.")

    numeric_cols = [c for c in frame.columns if c != "date"]
    for col in numeric_cols:
        frame[f"{col}_anom"] = monthly_anomaly(frame[col], frame["date"], baseline_start, baseline_end)
    keep = ["date", *[f"{col}_anom" for col in numeric_cols]]
    frame = frame[keep].merge(enso, on="date", how="left")
    frame["source_key"] = source_key

    out_dir = output_root / source_key
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "monthly_predictors.parquet"
    ordered = ["date", "source_key", *[c for c in frame.columns if c not in {"date", "source_key"}]]
    frame[ordered].to_parquet(out_path, index=False)
    print(f"[{source_key}] wrote {out_path}")
    return out_path


def parse_bbox(raw: str) -> tuple[float, float, float, float]:
    parts = [float(p.strip()) for p in raw.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("bbox must be lon_min,lat_min,lon_max,lat_max")
    return tuple(parts)  # type: ignore[return-value]


def parse_variable_map(values: list[str] | None) -> dict[str, str]:
    if not values:
        return {}
    out = {}
    for item in values:
        if "=" not in item:
            raise argparse.ArgumentTypeError("--var-map entries must be input_var=output_name")
        left, right = item.split("=", 1)
        out[left.strip()] = right.strip()
    return out


def expand_inputs(values: list[Path] | None) -> list[Path]:
    files: list[Path] = []
    for item in values or []:
        if item.is_dir():
            files.extend(sorted(path for path in item.rglob("*.nc*") if path.is_file()))
            continue
        matches = sorted(item.parent.glob(item.name)) if any(ch in str(item) for ch in "*?[") else [item]
        files.extend(path for path in matches if path.exists() and path.is_file())
    unique: dict[str, Path] = {}
    for path in files:
        unique[str(path.resolve())] = path
    return list(unique.values())


def helper_frame_from_spec(
    *,
    helper_name: str,
    input_values: list[Path],
    variable: str | None,
    output_name: str,
    bbox: tuple[float, float, float, float],
    progress_label: str | None = None,
    progress_start: int = 0,
    progress_total: int | None = None,
) -> pd.DataFrame:
    files = expand_inputs(input_values)
    if not files:
        raise RuntimeError(f"No input files found for helper {helper_name!r}.")
    return dataset_series_from_files(
        files=files,
        variable=variable,
        output_name=output_name,
        bbox=bbox,
        progress_label=progress_label,
        progress_start=progress_start,
        progress_total=progress_total,
    )


def prepare_from_config(config_path: Path, *, enso_override: Path | None = None) -> Path:
    raw = json.loads(config_path.read_text(encoding="utf-8"))
    source_key = str(raw.get("source") or "").strip().lower()
    if not source_key:
        raise ValueError("predictor config must define 'source'")
    use_helpers = str(raw.get("use_helpers", "yes")).strip().lower() not in {"no", "false", "0"}
    if not use_helpers:
        print(f"[{source_key}] helper predictors disabled in config; skipping build")
        return Path(raw.get("output_root") or DEFAULT_OUTPUT_ROOT) / source_key / "monthly_predictors.parquet"

    output_root = Path(raw.get("output_root") or DEFAULT_OUTPUT_ROOT)
    bbox = parse_bbox(str(raw.get("bbox", ",".join(str(v) for v in DEFAULT_BBOX))))
    baseline_start = str(raw.get("baseline_start") or "1981-01-01")
    baseline_end = str(raw.get("baseline_end") or "2010-12-31")
    enso_path = enso_override or (Path(raw["enso_file"]) if raw.get("enso_file") else None)
    enso = load_enso(enso_path)
    helper_specs = raw.get("helpers") or []
    if not isinstance(helper_specs, list) or not helper_specs:
        raise ValueError("predictor config must contain a non-empty 'helpers' list")
    enabled_specs = [
        spec
        for spec in helper_specs
        if isinstance(spec, dict) and str(spec.get("enabled", "true")).strip().lower() not in {"no", "false", "0"}
    ]
    total_files = 0
    helper_inputs_cache: list[tuple[dict[str, object], list[Path]]] = []
    for spec in enabled_specs:
        inputs = spec.get("input") or spec.get("inputs") or []
        if isinstance(inputs, (str, Path)):
            inputs = [inputs]
        input_values = [Path(str(item)) for item in inputs]
        files = expand_inputs(input_values)
        helper_inputs_cache.append((spec, files))
        total_files += len(files)
    total_files = max(total_files, 1)
    log_progress(
        f"[start] source={source_key} | helpers={len(enabled_specs)} | "
        f"files={total_files} | baseline={baseline_start}..{baseline_end}"
    )

    frame: pd.DataFrame | None = None
    processed_files = 0
    for helper_idx, (spec, files) in enumerate(helper_inputs_cache, start=1):
        helper_name = str(spec.get("name") or spec.get("output") or "").strip()
        output_name = str(spec.get("output") or helper_name).strip()
        if not files:
            raise RuntimeError(f"No input files found for helper {helper_name!r}.")
        log_progress(
            f"[helper {helper_idx}/{len(enabled_specs)}] {helper_name} -> {output_name} | files={len(files)}"
        )
        helper_frame = helper_frame_from_spec(
            helper_name=helper_name or output_name,
            input_values=files,
            variable=str(spec["variable"]).strip() if spec.get("variable") else None,
            output_name=output_name,
            bbox=bbox,
            progress_label=f"{source_key}/{helper_name}",
            progress_start=processed_files,
            progress_total=total_files,
        )
        processed_files += len(files)
        frame = helper_frame if frame is None else frame.merge(helper_frame, on="date", how="outer")
        log_progress(
            f"[merge] {helper_name}: merged columns={len(frame.columns)} | rows={len(frame):,} | "
            f"overall {processed_files}/{total_files} = {(100.0 * processed_files / total_files):0.1f}%"
        )

    if frame is None or frame.empty:
        raise RuntimeError(f"No helper predictors were prepared for {source_key}.")

    numeric_cols = [col for col in frame.columns if col != "date"]
    if "tmean" not in frame.columns and {"tmin", "tmax"}.issubset(frame.columns):
        frame["tmean"] = (pd.to_numeric(frame["tmin"], errors="coerce") + pd.to_numeric(frame["tmax"], errors="coerce")) / 2.0
        numeric_cols = [col for col in frame.columns if col != "date"]
    for col in numeric_cols:
        frame[f"{col}_anom"] = monthly_anomaly(frame[col], frame["date"], baseline_start, baseline_end)
    keep_cols = ["date", *[col for col in frame.columns if col.endswith("_anom")]]
    frame = frame[keep_cols].merge(enso, on="date", how="left")
    frame["source_key"] = source_key
    out_dir = output_root / source_key
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "monthly_predictors.parquet"
    ordered = ["date", "source_key", *[c for c in frame.columns if c not in {"date", "source_key"}]]
    frame[ordered].to_parquet(out_path, index=False)
    log_progress(f"[{source_key}] wrote {out_path}")
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare monthly prediction predictors from local inputs")
    parser.add_argument("--source", choices=["terraclimate", "agera5", "fldas2"], required=True)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--bbox", type=parse_bbox, default=DEFAULT_BBOX)
    parser.add_argument("--baseline-start", default="1981-01-01")
    parser.add_argument("--baseline-end", default="2010-12-31")
    parser.add_argument("--input", action="append", type=Path, help="Local NetCDF file, glob, or directory")
    parser.add_argument("--enso-file", type=Path, help="Optional local CSV/Parquet with date and enso_nino34 columns")
    parser.add_argument("--config", type=Path, help="Optional JSON config with separate helper folders and enabled flags")
    parser.add_argument("--use-helpers", choices=["yes", "no"], default="yes", help="Whether helper predictors should be built")
    parser.add_argument(
        "--var-map",
        action="append",
        help="NetCDF variable mapping, repeatable. Example: Rainf_tavg=precip",
    )
    args = parser.parse_args()

    if args.use_helpers == "no":
        print(f"[{args.source}] helper predictors disabled by CLI; skipping build")
        return
    if args.config:
        prepare_from_config(args.config, enso_override=args.enso_file)
        return

    output_root = Path(args.output_root)
    files = expand_inputs(args.input)
    enso = load_enso(args.enso_file)
    if args.source == "terraclimate":
        prepare_terraclimate(
            input_files=files,
            output_root=output_root,
            bbox=args.bbox,
            baseline_start=args.baseline_start,
            baseline_end=args.baseline_end,
            enso=enso,
        )
        return

    variable_map = parse_variable_map(args.var_map)
    if not variable_map:
        if args.source == "fldas2":
            variable_map = {
                "Rainf_tavg": "precip",
                "SoilMoi0_10cm_inst": "soil_moisture_top",
                "SoilMoi10_40cm_inst": "soil_moisture_10_40",
                "Tair_f_tavg": "tair",
            }
        else:
            variable_map = {
                "Precipitation_Flux": "precip",
                "Temperature_Air_2m_Mean_24h": "tmean",
            }
    prepare_from_netcdf(
        source_key=args.source,
        input_files=files,
        output_root=output_root,
        bbox=args.bbox,
        variable_map=variable_map,
        baseline_start=args.baseline_start,
        baseline_end=args.baseline_end,
        enso=enso,
    )


if __name__ == "__main__":
    main()
