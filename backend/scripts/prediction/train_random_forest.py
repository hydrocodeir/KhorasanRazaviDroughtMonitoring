"""Train Random Forest drought prediction models."""

from __future__ import annotations

import argparse
import os
import pickle
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor

from .train_lstm_attention import (
    DEFAULT_ARTIFACT_ROOT,
    DEFAULT_FEATURE_ROOT,
    DEFAULT_INTERVAL_METHOD,
    INTERVAL_LABELS,
    METHOD_NAME as LSTM_METHOD_NAME,
    aggregate_eval,
    build_interval_payload,
    build_feature_frame,
    discover_prediction_datasets,
    ensure_prediction_tables,
    get_available_indices,
    historical_uncertainty_spreads,
    lead_uncertainty_spreads,
    load_predictors,
    load_ts_frame,
    metrics_for,
    predictor_climatology,
    rmse_uncertainty_spreads,
    requested_prediction_indices,
    safe_model_key,
    write_outputs,
)

METHOD_NAME = "random_forest"


def log_progress(message: str) -> None:
    print(message, flush=True)


def predictor_columns(feature_columns: list[str]) -> list[str]:
    return [col for col in feature_columns if col != "y"]


def make_supervised_rows(
    frame: pd.DataFrame,
    feature_columns: list[str],
    input_window: int,
    cutoff: pd.Timestamp | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    cols = predictor_columns(feature_columns)
    xs: list[np.ndarray] = []
    ys: list[float] = []
    for _, group in frame.groupby("feature_id", sort=False):
        group = group.sort_values("date").reset_index(drop=True)
        for idx in range(input_window, len(group)):
            row = group.iloc[idx]
            target_date = pd.Timestamp(row["date"])
            if cutoff is not None and target_date > cutoff:
                continue
            x = pd.to_numeric(row[cols], errors="coerce").to_numpy(dtype=float)
            y = pd.to_numeric(row["y"], errors="coerce")
            if not np.isfinite(x).all() or not np.isfinite(y):
                continue
            xs.append(x.astype(np.float32))
            ys.append(float(y))
    if not xs:
        return np.empty((0, len(cols)), dtype=np.float32), np.empty((0,), dtype=np.float32)
    return np.stack(xs), np.array(ys, dtype=np.float32)


def month_feature_values(
    *,
    target_month: pd.Timestamp,
    work: pd.DataFrame,
    feature_columns: list[str],
    climatology: dict[int, dict[str, float]],
) -> dict[str, float]:
    y_values = pd.to_numeric(work["y"], errors="coerce").dropna().to_numpy(dtype=float)
    latest_y = float(y_values[-1]) if len(y_values) else 0.0
    values: dict[str, float] = {}
    for col in predictor_columns(feature_columns):
        if col == "month_sin":
            values[col] = float(np.sin(2.0 * np.pi * int(target_month.month) / 12.0))
        elif col == "month_cos":
            values[col] = float(np.cos(2.0 * np.pi * int(target_month.month) / 12.0))
        elif col.startswith("y_lag_"):
            try:
                lag = int(col.rsplit("_", 1)[-1])
            except ValueError:
                lag = 1
            values[col] = float(y_values[-lag]) if len(y_values) >= lag else latest_y
        else:
            values[col] = float(climatology.get(int(target_month.month), {}).get(col, 0.0))
    return values


def recursive_forecast_rf(
    model: RandomForestRegressor,
    history: pd.DataFrame,
    feature_columns: list[str],
    horizon: int,
    climatology: dict[int, dict[str, float]],
) -> list[dict[str, Any]]:
    work = history.sort_values("date").copy()
    if work.empty:
        return []
    last_month = pd.Timestamp(work["date"].max())
    out: list[dict[str, Any]] = []
    for lead in range(1, horizon + 1):
        target_month = pd.Timestamp(date(last_month.year, last_month.month, 1)) + pd.DateOffset(months=lead)
        feature_values = month_feature_values(
            target_month=target_month,
            work=work,
            feature_columns=feature_columns,
            climatology=climatology,
        )
        x = np.array([[feature_values[col] for col in predictor_columns(feature_columns)]], dtype=np.float32)
        pred = float(model.predict(x)[0])
        row = {"date": target_month, "y": pred, **feature_values}
        work = pd.concat([work, pd.DataFrame([row])], ignore_index=True)
        out.append({"date": target_month.date(), "lead_month": lead, "value": pred})
    return out


def backtest_recursive_rf(
    model: RandomForestRegressor,
    frame: pd.DataFrame,
    feature_columns: list[str],
    input_window: int,
    horizon: int,
    climatology: dict[int, dict[str, float]],
    cutoff: pd.Timestamp,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    groups = list(frame.groupby("feature_id", sort=False))
    total_groups = len(groups) or 1
    for group_idx, (feature_id, group) in enumerate(groups, start=1):
        if group_idx == 1 or group_idx % 10 == 0 or group_idx == total_groups:
            log_progress(
                f"[backtest] features {group_idx}/{total_groups} = {(100.0 * group_idx / total_groups):0.1f}%"
            )
        group = group.sort_values("date")
        origins = group[(group["date"] >= cutoff) & (group["date"] <= group["date"].max() - pd.DateOffset(months=horizon))]
        for origin in origins["date"].iloc[::3]:
            hist = group[group["date"] <= origin]
            if len(hist) < input_window:
                continue
            forecasts = recursive_forecast_rf(model, hist, feature_columns, horizon, climatology)
            actual_map = {
                pd.Timestamp(row.date).date(): float(row.y)
                for row in group[["date", "y"]].itertuples(index=False)
                if pd.notna(row.y)
            }
            for item in forecasts:
                actual = actual_map.get(item["date"])
                if actual is None:
                    continue
                rows.append(
                    {
                        "feature_id": str(feature_id),
                        "lead_month": int(item["lead_month"]),
                        "target_date": item["date"],
                        "actual": float(actual),
                        "predicted": float(item["value"]),
                    }
                )
    return pd.DataFrame(rows)


def train_group(
    *,
    source_key: str,
    datasets: list[Any],
    index: str,
    feature_root: Path,
    artifact_root: Path,
    input_window: int,
    horizon: int,
    backtest_months: int,
    n_estimators: int,
    max_depth: int | None,
    min_samples_leaf: int,
    use_helpers: bool,
) -> None:
    predictors = load_predictors(feature_root, source_key, use_helpers=use_helpers)
    climatology = predictor_climatology(predictors)
    log_progress(
        f"[{source_key}/{index}/{METHOD_NAME}] predictors rows={len(predictors):,} | "
        f"columns={len(predictors.columns) if not predictors.empty else 0}"
    )
    frames: list[pd.DataFrame] = []
    dataset_frames: dict[str, pd.DataFrame] = {}
    feature_reports: dict[str, dict[str, Any]] = {}
    feature_columns: list[str] | None = None
    for ds in datasets:
        ts = load_ts_frame(ds.key, index)
        if ts.empty:
            print(f"[{ds.key}] no data for {index}; skipped")
            continue
        frame, ds_feature_columns, report = build_feature_frame(ts, predictors)
        if feature_columns is None:
            feature_columns = ds_feature_columns
        else:
            unavailable = [col for col in feature_columns if col not in ds_feature_columns]
            for col in unavailable:
                frame[col] = 0.0
            extra = [col for col in ds_feature_columns if col not in feature_columns and col != "y"]
            if extra:
                report["helper_columns_dropped"] = sorted(set(report.get("helper_columns_dropped", []) + extra))
                report["helper_columns_used"] = [col for col in report.get("helper_columns_used", []) if col in feature_columns]
        frame["dataset_key"] = ds.key
        frames.append(frame)
        dataset_frames[ds.key] = frame
        feature_reports[ds.key] = report

    if not frames or feature_columns is None:
        print(f"[{source_key}/{index}] no trainable datasets for {METHOD_NAME}")
        return

    combined = pd.concat(frames, ignore_index=True)
    max_train_date = pd.Timestamp(combined["date"].max())
    cutoff = max_train_date - pd.DateOffset(months=backtest_months)
    log_progress(
        f"[{source_key}/{index}/{METHOD_NAME}] building evaluation rows from "
        f"{len(frames):,} dataset frame(s), total rows={len(combined):,}"
    )
    train_x, train_y = make_supervised_rows(combined, feature_columns, input_window, cutoff=cutoff)
    if len(train_x) < 32:
        print(f"[{source_key}/{index}/{METHOD_NAME}] too few rows ({len(train_x)}); skipped")
        return
    log_progress(
        f"[{source_key}/{index}/{METHOD_NAME}] fitting evaluation forest on "
        f"{len(train_x):,} rows | features={train_x.shape[1]} | trees={max(50, int(n_estimators))}"
    )

    eval_model = RandomForestRegressor(
        n_estimators=max(50, int(n_estimators)),
        max_depth=max_depth if max_depth and max_depth > 0 else None,
        min_samples_leaf=max(1, int(min_samples_leaf)),
        n_jobs=max(1, int(os.getenv("PREDICTION_SKLEARN_JOBS", "2"))),
        random_state=42,
    )
    eval_model.fit(train_x, train_y)
    log_progress(f"[{source_key}/{index}/{METHOD_NAME}] evaluation forest fit complete")

    eval_by_dataset: dict[str, list[dict[str, Any]]] = {}
    all_eval_rows: list[pd.DataFrame] = []
    for ds_idx, ds in enumerate(datasets, start=1):
        frame = dataset_frames.get(ds.key)
        if frame is None:
            continue
        log_progress(
            f"[{source_key}/{index}/{METHOD_NAME}] backtesting dataset {ds_idx}/{len(datasets)}: {ds.key}"
        )
        bt = backtest_recursive_rf(eval_model, frame, feature_columns, input_window, horizon, climatology, cutoff)
        if not bt.empty:
            bt["dataset_key"] = ds.key
            all_eval_rows.append(bt)
        log_progress(
            f"[{source_key}/{index}/{METHOD_NAME}] backtest done for {ds.key}: rows={len(bt):,}"
        )
        eval_by_dataset[ds.key] = aggregate_eval(bt)

    log_progress(f"[{source_key}/{index}/{METHOD_NAME}] building final training rows")
    full_x, full_y = make_supervised_rows(combined, feature_columns, input_window, cutoff=None)
    log_progress(
        f"[{source_key}/{index}/{METHOD_NAME}] fitting final forest on "
        f"{len(full_x):,} rows | features={full_x.shape[1]} | trees={max(50, int(n_estimators))}"
    )
    final_model = RandomForestRegressor(
        n_estimators=max(50, int(n_estimators)),
        max_depth=max_depth if max_depth and max_depth > 0 else None,
        min_samples_leaf=max(1, int(min_samples_leaf)),
        n_jobs=max(1, int(os.getenv("PREDICTION_SKLEARN_JOBS", "2"))),
        random_state=42,
    )
    final_model.fit(full_x, full_y)
    log_progress(f"[{source_key}/{index}/{METHOD_NAME}] final forest fit complete")

    artifact_root.mkdir(parents=True, exist_ok=True)
    model_key = safe_model_key(source_key, index, METHOD_NAME)
    version_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    version_key = f"{model_key}_{version_stamp}"
    artifact_path = artifact_root / f"{model_key}.pkl"
    versioned_artifact_path = artifact_root / f"{version_key}.pkl"
    with versioned_artifact_path.open("wb") as fh:
        pickle.dump(
            {
                "model": final_model,
                "model_key": model_key,
                "version_key": version_key,
                "method_name": METHOD_NAME,
                "feature_columns": feature_columns,
                "created_at": version_stamp,
            },
            fh,
        )
    with artifact_path.open("wb") as fh:
        pickle.dump(
            {
                "model": final_model,
                "model_key": model_key,
                "version_key": version_key,
                "method_name": METHOD_NAME,
                "feature_columns": feature_columns,
                "created_at": version_stamp,
            },
            fh,
        )

    pooled_backtest = pd.concat(all_eval_rows, ignore_index=True) if all_eval_rows else pd.DataFrame()
    summary_metrics = (
        metrics_for(pooled_backtest["actual"].to_numpy(float), pooled_backtest["predicted"].to_numpy(float))
        if not pooled_backtest.empty
        else {}
    )

    forecasts_by_dataset: dict[str, list[dict[str, Any]]] = {}
    issue_dates: dict[str, date] = {}
    for ds_idx, ds in enumerate(datasets, start=1):
        frame = dataset_frames.get(ds.key)
        if frame is None:
            continue
        forecast_rows: list[dict[str, Any]] = []
        issue_dates[ds.key] = pd.Timestamp(frame["date"].max()).date()
        dataset_backtest = (
            pooled_backtest[pooled_backtest["dataset_key"] == ds.key].copy()
            if not pooled_backtest.empty and "dataset_key" in pooled_backtest.columns
            else pd.DataFrame()
        )
        uncertainty_spreads = lead_uncertainty_spreads(
            dataset_backtest=dataset_backtest,
            pooled_backtest=pooled_backtest,
            history=frame,
            horizon=horizon,
        )
        rmse_spreads = rmse_uncertainty_spreads(
            dataset_backtest=dataset_backtest,
            pooled_backtest=pooled_backtest,
            history=frame,
            horizon=horizon,
        )
        historical_spreads = historical_uncertainty_spreads(history=frame, horizon=horizon)
        interval_spreads = {
            "backtest_q90": uncertainty_spreads,
            "rmse_164": rmse_spreads,
            "historical_spread": historical_spreads,
        }
        groups = list(frame.groupby("feature_id", sort=False))
        total_groups = len(groups) or 1
        log_progress(
            f"[{source_key}/{index}/{METHOD_NAME}] forecasting dataset {ds_idx}/{len(datasets)}: "
            f"{ds.key} | features={total_groups}"
        )
        for group_idx, (feature_id, group) in enumerate(groups, start=1):
            if group_idx == 1 or group_idx % 10 == 0 or group_idx == total_groups:
                log_progress(
                    f"[{source_key}/{index}/{METHOD_NAME}] {ds.key}: feature {group_idx}/{total_groups} "
                    f"= {(100.0 * group_idx / total_groups):0.1f}%"
                )
            preds = recursive_forecast_rf(final_model, group, feature_columns, horizon, climatology)
            for item in preds:
                spread = uncertainty_spreads.get(int(item["lead_month"]), 0.15)
                intervals = build_interval_payload(
                    value=item["value"],
                    lead_month=int(item["lead_month"]),
                    interval_spreads=interval_spreads,
                )
                forecast_rows.append(
                    {
                        "feature_id": str(feature_id),
                        "target_date": item["date"],
                        "lead_month": item["lead_month"],
                        "value": item["value"],
                        "lower": item["value"] - spread,
                        "upper": item["value"] + spread,
                        "intervals": intervals,
                    }
                )
        forecasts_by_dataset[ds.key] = forecast_rows
        log_progress(
            f"[{source_key}/{index}/{METHOD_NAME}] forecast done for {ds.key}: rows={len(forecast_rows):,}"
        )

    log_progress(f"[{source_key}/{index}/{METHOD_NAME}] writing outputs to database and artifacts")
    write_outputs(
        method_name=METHOD_NAME,
        model_key=model_key,
        source_key=source_key,
        index=index,
        input_window=input_window,
        horizon=horizon,
        artifact_path=artifact_path,
        feature_columns=feature_columns,
        training_params={
            "method_name": METHOD_NAME,
            "n_estimators": n_estimators,
            "max_depth": max_depth,
            "min_samples_leaf": min_samples_leaf,
            "backtest_months": backtest_months,
            "adaptive_inputs": {
                "use_helpers": use_helpers,
                "dataset_reports": feature_reports,
                "pooled_feature_columns": feature_columns,
            },
            "uncertainty": {
                "method": "lead-wise residual-derived intervals with selectable display methods",
                "default_interval_method": DEFAULT_INTERVAL_METHOD,
                "available_interval_methods": ["backtest_q90", "rmse_164", "historical_spread"],
                "interval_labels": {key: INTERVAL_LABELS[key] for key in ("backtest_q90", "rmse_164", "historical_spread")},
                "interval_descriptions": {
                    "backtest_q90": "Lead-wise absolute residual quantile with pooled and historical fallback.",
                    "rmse_164": "Lead-wise RMSE-scaled interval using 1.64 x RMSE with fallback.",
                    "historical_spread": "Historical target spread scaled by forecast lead.",
                },
            },
        },
        summary_metrics=summary_metrics,
        forecasts_by_dataset=forecasts_by_dataset,
        eval_by_dataset=eval_by_dataset,
        issue_dates=issue_dates,
        version_key=version_key,
        versioned_artifact_path=versioned_artifact_path,
    )
    log_progress(f"[{source_key}/{index}/{METHOD_NAME}] completed successfully")


def main() -> None:
    parser = argparse.ArgumentParser(description="Train Random Forest drought prediction models")
    parser.add_argument("--dataset", help="Train only one non-station dataset")
    parser.add_argument("--source", help="Train only one source_key")
    parser.add_argument("--index", action="append", help="Index to train, repeatable")
    parser.add_argument("--scale", action="append", type=int, help="Train SPI forecast for this scale; repeatable")
    parser.add_argument("--feature-root", default=str(DEFAULT_FEATURE_ROOT))
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument("--input-window", type=int, default=18)
    parser.add_argument("--horizon", type=int, default=12)
    parser.add_argument("--backtest-months", type=int, default=36)
    parser.add_argument("--rf-trees", type=int, default=300)
    parser.add_argument("--rf-max-depth", type=int, default=10)
    parser.add_argument("--rf-min-samples-leaf", type=int, default=2)
    parser.add_argument("--use-helpers", choices=["auto", "yes", "no"], default="auto")
    args = parser.parse_args()

    ensure_prediction_tables()
    datasets = discover_prediction_datasets(args.dataset)
    if args.source:
        datasets = [ds for ds in datasets if ds.source_key == args.source.lower()]
    if not datasets:
        raise SystemExit("No non-station datasets found for prediction training.")

    groups: dict[str, list[Any]] = {}
    for ds in datasets:
        groups.setdefault(ds.source_key, []).append(ds)

    for source_key, group in groups.items():
        available = sorted(set.intersection(*(set(get_available_indices(ds.key)) for ds in group)))
        indices = requested_prediction_indices(args.index, args.scale, available)
        for idx in indices:
            if idx not in available:
                print(f"[{source_key}/{idx}/{METHOD_NAME}] missing in one or more datasets; skipped")
                continue
            train_group(
                source_key=source_key,
                datasets=group,
                index=idx,
                feature_root=Path(args.feature_root),
                artifact_root=Path(args.artifact_root),
                input_window=args.input_window,
                horizon=args.horizon,
                backtest_months=args.backtest_months,
                n_estimators=args.rf_trees,
                max_depth=args.rf_max_depth if args.rf_max_depth > 0 else None,
                min_samples_leaf=args.rf_min_samples_leaf,
                use_helpers=args.use_helpers != "no",
            )


if __name__ == "__main__":
    main()
