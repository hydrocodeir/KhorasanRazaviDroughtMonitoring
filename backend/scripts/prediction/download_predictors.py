"""Prepare monthly predictors for drought prediction from local files.

Outputs one compact file per source:

    data/prediction/features/<source_key>/monthly_predictors.parquet

The training script joins these predictors by month. If a source is not yet
configured, the training pipeline still works from the drought-index history
alone, but these files enable the full multivariate LSTM+attention method.
"""

from __future__ import annotations

import argparse
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import pandas as pd
import xarray as xr


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_OUTPUT_ROOT = ROOT / "data" / "prediction" / "features"
DEFAULT_BBOX = (56.0, 33.0, 62.5, 38.5)  # lon_min, lat_min, lon_max, lat_max
ENSO_URL = "https://psl.noaa.gov/data/correlation/nina34.data"


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
        ds = xr.open_mfdataset([str(path) for path in sorted(matches)], combine="by_coords")
        data_var = var if var in ds.data_vars else next(iter(ds.data_vars))
        series = area_weighted_mean(ds[data_var], bbox).to_dataframe(name=out_col).reset_index()
        time_col = "time" if "time" in series.columns else "date"
        series["date"] = month_start(series[time_col])
        series = series.groupby("date", as_index=False)[out_col].mean()
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
    ds = xr.open_mfdataset([str(path) for path in input_files], combine="by_coords")
    frame: pd.DataFrame | None = None
    for nc_var, out_name in variable_map.items():
        if nc_var not in ds:
            raise ValueError(f"{nc_var!r} not found in {source_key} files")
        series = area_weighted_mean(ds[nc_var], bbox).to_dataframe(name=out_name).reset_index()
        time_col = "time" if "time" in series.columns else "date"
        series["date"] = month_start(series[time_col])
        series = series.groupby("date", as_index=False)[out_name].mean()
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare monthly prediction predictors from local inputs")
    parser.add_argument("--source", choices=["terraclimate", "agera5", "fldas2"], required=True)
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--bbox", type=parse_bbox, default=DEFAULT_BBOX)
    parser.add_argument("--baseline-start", default="1981-01-01")
    parser.add_argument("--baseline-end", default="2010-12-31")
    parser.add_argument("--input", action="append", type=Path, help="Local NetCDF file, glob, or directory")
    parser.add_argument("--enso-file", type=Path, help="Optional local CSV/Parquet with date and enso_nino34 columns")
    parser.add_argument(
        "--var-map",
        action="append",
        help="NetCDF variable mapping, repeatable. Example: Rainf_tavg=precip",
    )
    args = parser.parse_args()

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
