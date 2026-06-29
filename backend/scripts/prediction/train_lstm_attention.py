"""Train LSTM+attention drought prediction models.

Method
------
The workflow follows the UCI drought prediction idea at dashboard scale:

* 18 monthly time steps are used as model input.
* A lightweight LSTM encodes the sequence.
* Additive attention learns which past months matter most.
* Forecasts are generated autoregressively for the next 12 months.
* Backtests report lead-wise MAE, RMSE, bias, R2/correlation, and drought
  class accuracy.

Models are pooled by ``source_key + index`` and then forecasts/evaluation are
stored per dataset. Station layers are skipped.
"""

from __future__ import annotations

import argparse
import shutil
import json
import math
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import text

try:
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, TensorDataset
except ImportError as exc:  # pragma: no cover - exercised by CLI users
    raise SystemExit("PyTorch is required. Install backend requirements first.") from exc

ROOT = Path(__file__).resolve().parents[3]
import sys

sys.path.insert(0, str((ROOT / "backend").resolve()))
from app.database import engine  # noqa: E402
from app.datasets_store import _json_safe_float, get_available_indices, validate_index_name  # noqa: E402
from app.prediction_store import ensure_prediction_tables  # noqa: E402
from app.utils import drought_class  # noqa: E402


DEFAULT_FEATURE_ROOT = ROOT / "data" / "prediction" / "features"
DEFAULT_ARTIFACT_ROOT = ROOT / "data" / "prediction" / "models"
BASE_INPUT_COLUMNS = ["y", "y_lag_1", "y_lag_3", "y_lag_6", "month_sin", "month_cos"]
HELPER_MIN_COVERAGE = 0.02
METHOD_NAME = "lstm_attention"
DEFAULT_INTERVAL_METHOD = "backtest_q90"
INTERVAL_LABELS = {
    "backtest_q90": "Backtest Q90",
    "rmse_164": "RMSE x 1.64",
    "historical_spread": "Historical Spread",
    "sigma_model": "Sigma Model",
}


@dataclass(frozen=True)
class DatasetInfo:
    key: str
    title: str
    source_key: str
    boundary_key: str
    min_date: date | None
    max_date: date | None


@dataclass
class Standardizer:
    mean: np.ndarray
    std: np.ndarray

    def transform(self, values: np.ndarray) -> np.ndarray:
        return (values - self.mean) / self.std

    def inverse_y(self, values: np.ndarray | float) -> np.ndarray | float:
        return (values * self.std[0]) + self.mean[0]

    def to_dict(self) -> dict[str, list[float]]:
        return {"mean": self.mean.tolist(), "std": self.std.tolist()}


class LSTMAttention(nn.Module):
    def __init__(self, input_size: int, hidden_size: int, dropout: float) -> None:
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_size,
            hidden_size=hidden_size,
            batch_first=True,
            dropout=dropout if hidden_size > 1 else 0.0,
        )
        self.attention = nn.Sequential(
            nn.Linear(hidden_size, hidden_size),
            nn.Tanh(),
            nn.Linear(hidden_size, 1),
        )
        self.head = nn.Sequential(
            nn.Linear(hidden_size, max(8, hidden_size // 2)),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(max(8, hidden_size // 2), 1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        states, _ = self.lstm(x)
        weights = torch.softmax(self.attention(states), dim=1)
        context = torch.sum(states * weights, dim=1)
        return self.head(context).squeeze(-1)


def add_month(month: date, offset: int) -> date:
    y = month.year + ((month.month - 1 + offset) // 12)
    m = ((month.month - 1 + offset) % 12) + 1
    return date(y, m, 1)


def safe_model_key(source_key: str, index: str, method_name: str = METHOD_NAME) -> str:
    return f"{source_key}_{index}_{method_name}".replace("-", "_").lower()


def requested_prediction_indices(
    explicit_indices: list[str] | None,
    scales: list[int] | None,
    available: list[str],
) -> list[str]:
    requested: list[str] = []
    for idx in explicit_indices or []:
        key = str(idx).strip().lower()
        if key and key not in requested:
            requested.append(key)
    for scale in scales or []:
        key = f"spi{max(1, int(scale))}"
        if key not in requested:
            requested.append(key)
    if requested:
        return requested
    return [idx for idx in available if idx.startswith(("spi", "spei", "ssi"))]


def discover_prediction_datasets(dataset: str | None = None) -> list[DatasetInfo]:
    sql = text(
        """
        SELECT dataset_key, COALESCE(title, dataset_key) AS title,
               min_date, max_date, metadata
        FROM datasets
        ORDER BY dataset_key
        """
    )
    with engine.begin() as conn:
        rows = conn.execute(sql).fetchall()

    out: list[DatasetInfo] = []
    for row in rows:
        meta = row.metadata or {}
        boundary_key = str(meta.get("boundary_key") or "").lower()
        source_key = str(meta.get("source_key") or row.dataset_key).lower()
        if boundary_key == "station" or "station" in str(row.dataset_key).lower():
            continue
        if dataset and str(row.dataset_key).lower() != dataset.lower():
            continue
        out.append(
            DatasetInfo(
                key=str(row.dataset_key),
                title=str(row.title),
                source_key=source_key,
                boundary_key=boundary_key,
                min_date=row.min_date,
                max_date=row.max_date,
            )
        )
    return out


def load_ts_frame(dataset_key: str, index: str) -> pd.DataFrame:
    idx = validate_index_name(dataset_key, index)
    table = f"ts_{dataset_key.lower()}"
    sql = text(
        f"""
        SELECT feature_id, date, "{idx}" AS y
        FROM {table}
        WHERE "{idx}" IS NOT NULL
        ORDER BY feature_id, date
        """
    )
    with engine.begin() as conn:
        frame = pd.read_sql(sql, conn)
    if frame.empty:
        return frame
    frame["date"] = pd.to_datetime(frame["date"]).dt.to_period("M").dt.to_timestamp()
    frame["y"] = pd.to_numeric(frame["y"], errors="coerce")
    return frame.dropna(subset=["feature_id", "date", "y"])


def predictor_path(feature_root: Path, source_key: str) -> Path:
    return feature_root / source_key / "monthly_predictors.parquet"


def load_predictors(feature_root: Path, source_key: str, *, use_helpers: bool = True) -> pd.DataFrame:
    if not use_helpers:
        return pd.DataFrame(columns=["date"])
    path = predictor_path(feature_root, source_key)
    if not path.exists():
        return pd.DataFrame(columns=["date"])
    frame = pd.read_parquet(path)
    if "date" not in frame.columns:
        raise ValueError(f"{path} must contain a date column")
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"]).dt.to_period("M").dt.to_timestamp()
    if "source_key" in frame.columns:
        frame = frame[frame["source_key"].astype(str).str.lower() == source_key.lower()]
    for col in list(frame.columns):
        if col in {"date", "source_key"}:
            continue
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    return frame


def predictor_climatology(predictors: pd.DataFrame) -> dict[int, dict[str, float]]:
    if predictors.empty:
        return {}
    cols = [c for c in predictors.columns if c not in {"date", "source_key"}]
    if not cols:
        return {}
    work = predictors.copy()
    work["month"] = work["date"].dt.month
    out: dict[int, dict[str, float]] = {}
    for month, group in work.groupby("month"):
        out[int(month)] = {
            col: float(group[col].dropna().mean()) if group[col].notna().any() else 0.0
            for col in cols
        }
    return out


def build_feature_frame(
    ts: pd.DataFrame,
    predictors: pd.DataFrame,
    *,
    min_helper_coverage: float = HELPER_MIN_COVERAGE,
) -> tuple[pd.DataFrame, list[str], dict[str, Any]]:
    """Build adaptive model inputs.

    Baseline inputs are always available:
      - current drought index value inside the 18-month sequence
      - explicit 1/3/6-month target lags
      - seasonal sine/cosine terms

    Helper predictors are optional. Any numeric helper column with enough
    coverage is used; missing helper columns are dropped without blocking
    training. This keeps prediction available when all, some, or none of the
    auxiliary datasets have been prepared.
    """

    frame = ts.copy()
    helper_candidates: list[str] = []
    if not predictors.empty:
        helper_candidates = [
            c
            for c in predictors.columns
            if c not in {"date", "source_key"} and pd.api.types.is_numeric_dtype(predictors[c])
        ]
        frame = frame.merge(predictors, on="date", how="left")

    frame = frame.sort_values(["feature_id", "date"])
    for lag in (1, 3, 6):
        lag_col = f"y_lag_{lag}"
        frame[lag_col] = frame.groupby("feature_id")["y"].shift(lag)
        frame[lag_col] = frame[lag_col].fillna(frame["y"])

    frame["month_sin"] = np.sin(2.0 * np.pi * frame["date"].dt.month / 12.0)
    frame["month_cos"] = np.cos(2.0 * np.pi * frame["date"].dt.month / 12.0)

    used_helpers: list[str] = []
    dropped_helpers: list[str] = []
    helper_coverage: dict[str, float] = {}
    for col in helper_candidates:
        coverage = float(frame[col].notna().mean()) if col in frame else 0.0
        helper_coverage[col] = coverage
        if coverage < min_helper_coverage:
            dropped_helpers.append(col)
            continue
        frame[col] = frame[col].fillna(frame.groupby(frame["date"].dt.month)[col].transform("mean"))
        frame[col] = frame[col].fillna(frame[col].median()).fillna(0.0)
        used_helpers.append(col)

    for col in BASE_INPUT_COLUMNS:
        frame[col] = pd.to_numeric(frame[col], errors="coerce").fillna(0.0)

    report = {
        "mode": "multivariate" if used_helpers else "target_lags_only",
        "base_columns": list(BASE_INPUT_COLUMNS),
        "helper_columns_used": used_helpers,
        "helper_columns_dropped": dropped_helpers,
        "helper_coverage": helper_coverage,
        "min_helper_coverage": min_helper_coverage,
    }

    return frame.sort_values(["feature_id", "date"]), [*BASE_INPUT_COLUMNS, *used_helpers], report


def fit_standardizer(frame: pd.DataFrame, feature_columns: list[str]) -> Standardizer:
    values = frame[feature_columns].to_numpy(dtype=np.float32)
    mean = np.nanmean(values, axis=0)
    std = np.nanstd(values, axis=0)
    std = np.where(np.isfinite(std) & (std > 1e-6), std, 1.0)
    mean = np.where(np.isfinite(mean), mean, 0.0)
    return Standardizer(mean=mean.astype(np.float32), std=std.astype(np.float32))


def make_sequences(
    frame: pd.DataFrame,
    feature_columns: list[str],
    scaler: Standardizer,
    input_window: int,
    cutoff: pd.Timestamp | None = None,
) -> tuple[np.ndarray, np.ndarray, list[pd.Timestamp]]:
    xs: list[np.ndarray] = []
    ys: list[float] = []
    target_dates: list[pd.Timestamp] = []
    for _, group in frame.groupby("feature_id", sort=False):
        group = group.sort_values("date")
        months = group["date"].tolist()
        values = scaler.transform(group[feature_columns].to_numpy(dtype=np.float32))
        raw_y = group["y"].to_numpy(dtype=np.float32)
        for end in range(input_window, len(group)):
            target_date = pd.Timestamp(months[end])
            if cutoff is not None and target_date > cutoff:
                continue
            if not np.isfinite(raw_y[end]):
                continue
            window = values[end - input_window : end]
            if not np.isfinite(window).all():
                continue
            xs.append(window.astype(np.float32))
            ys.append(float((raw_y[end] - scaler.mean[0]) / scaler.std[0]))
            target_dates.append(target_date)
    if not xs:
        return np.empty((0, input_window, len(feature_columns)), dtype=np.float32), np.empty((0,), dtype=np.float32), []
    return np.stack(xs), np.array(ys, dtype=np.float32), target_dates


def train_model(
    x: np.ndarray,
    y: np.ndarray,
    *,
    hidden_size: int,
    dropout: float,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    initial_model: LSTMAttention | None = None,
) -> LSTMAttention:
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    model = LSTMAttention(input_size=x.shape[-1], hidden_size=hidden_size, dropout=dropout).to(device)
    if initial_model is not None:
        model.load_state_dict(initial_model.state_dict())
    dataset = TensorDataset(torch.from_numpy(x), torch.from_numpy(y))
    loader = DataLoader(dataset, batch_size=batch_size, shuffle=True)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=1e-4)
    loss_fn = nn.SmoothL1Loss()
    model.train()
    for _ in range(max(1, epochs)):
        for xb, yb in loader:
            xb = xb.to(device)
            yb = yb.to(device)
            optimizer.zero_grad(set_to_none=True)
            loss = loss_fn(model(xb), yb)
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
    return model.cpu()


def load_warm_start_model(
    *,
    artifact_path: Path,
    feature_columns: list[str],
    input_window: int,
    horizon: int,
    hidden_size: int,
    dropout: float,
) -> LSTMAttention | None:
    """Load a previous compatible model for periodic self-learning."""

    if not artifact_path.exists():
        return None
    try:
        try:
            checkpoint = torch.load(artifact_path, map_location="cpu", weights_only=True)
        except TypeError:
            checkpoint = torch.load(artifact_path, map_location="cpu")
    except Exception as exc:
        print(f"[warm-start] could not load {artifact_path}: {exc}")
        return None
    if checkpoint.get("feature_columns") != feature_columns:
        print("[warm-start] skipped: feature columns changed")
        return None
    if int(checkpoint.get("input_window", -1)) != input_window or int(checkpoint.get("horizon", -1)) != horizon:
        print("[warm-start] skipped: input window or horizon changed")
        return None
    if int(checkpoint.get("hidden_size", -1)) != hidden_size:
        print("[warm-start] skipped: hidden size changed")
        return None

    model = LSTMAttention(input_size=len(feature_columns), hidden_size=hidden_size, dropout=dropout)
    try:
        model.load_state_dict(checkpoint["model_state"])
    except Exception as exc:
        print(f"[warm-start] skipped: incompatible state dict ({exc})")
        return None
    print(f"[warm-start] loaded previous weights from {artifact_path}")
    return model


def predict_one(model: LSTMAttention, window: np.ndarray) -> float:
    model.eval()
    with torch.no_grad():
        tensor = torch.from_numpy(window.astype(np.float32)).unsqueeze(0)
        return float(model(tensor).item())


def month_predictor_values(
    month: pd.Timestamp,
    predictor_cols: list[str],
    climatology: dict[int, dict[str, float]],
) -> dict[str, float]:
    values = climatology.get(int(month.month), {})
    return {col: float(values.get(col, 0.0)) for col in predictor_cols}


def future_input_values(
    *,
    target_month: pd.Timestamp,
    work: pd.DataFrame,
    feature_columns: list[str],
    climatology: dict[int, dict[str, float]],
) -> dict[str, float]:
    values: dict[str, float] = {}
    y_values = pd.to_numeric(work["y"], errors="coerce").dropna().to_numpy(dtype=float)
    latest_y = float(y_values[-1]) if len(y_values) else 0.0
    for col in feature_columns:
        if col == "y":
            continue
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


def recursive_forecast(
    model: LSTMAttention,
    history: pd.DataFrame,
    feature_columns: list[str],
    scaler: Standardizer,
    input_window: int,
    horizon: int,
    climatology: dict[int, dict[str, float]],
) -> list[dict[str, Any]]:
    work = history.sort_values("date").tail(input_window).copy()
    if len(work) < input_window:
        return []

    out: list[dict[str, Any]] = []
    last_month = pd.Timestamp(work["date"].max())
    for lead in range(1, horizon + 1):
        values = scaler.transform(work[feature_columns].tail(input_window).to_numpy(dtype=np.float32))
        pred_scaled = predict_one(model, values)
        pred = float(scaler.inverse_y(pred_scaled))
        target_month = pd.Timestamp(add_month(last_month.date(), lead))
        row: dict[str, Any] = {"date": target_month, "y": pred}
        row.update(
            future_input_values(
                target_month=target_month,
                work=work,
                feature_columns=feature_columns,
                climatology=climatology,
            )
        )
        work = pd.concat([work, pd.DataFrame([row])], ignore_index=True)
        out.append({"date": target_month.date(), "lead_month": lead, "value": pred})
    return out


def metrics_for(actual: np.ndarray, pred: np.ndarray) -> dict[str, float | int | None]:
    mask = np.isfinite(actual) & np.isfinite(pred)
    if not mask.any():
        return {
            "mae": None,
            "rmse": None,
            "bias": None,
            "r2": None,
            "correlation": None,
            "drought_class_accuracy": None,
            "sample_count": 0,
        }
    a = actual[mask]
    p = pred[mask]
    err = p - a
    ss_res = float(np.sum(err**2))
    ss_tot = float(np.sum((a - np.mean(a)) ** 2))
    corr = float(np.corrcoef(a, p)[0, 1]) if len(a) > 1 and np.std(a) > 0 and np.std(p) > 0 else None
    acc = float(np.mean([drought_class(x) == drought_class(y) for x, y in zip(a, p)]))
    return {
        "mae": float(np.mean(np.abs(err))),
        "rmse": float(math.sqrt(np.mean(err**2))),
        "bias": float(np.mean(err)),
        "r2": float(1.0 - (ss_res / ss_tot)) if ss_tot > 0 else None,
        "correlation": corr,
        "drought_class_accuracy": acc,
        "sample_count": int(len(a)),
    }


def backtest_recursive(
    model: LSTMAttention,
    frame: pd.DataFrame,
    feature_columns: list[str],
    scaler: Standardizer,
    input_window: int,
    horizon: int,
    climatology: dict[int, dict[str, float]],
    cutoff: pd.Timestamp,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for feature_id, group in frame.groupby("feature_id", sort=False):
        group = group.sort_values("date")
        origins = group[(group["date"] >= cutoff) & (group["date"] <= group["date"].max() - pd.DateOffset(months=horizon))]
        for origin in origins["date"].iloc[::3]:
            hist = group[group["date"] <= origin]
            if len(hist) < input_window:
                continue
            forecasts = recursive_forecast(
                model,
                hist,
                feature_columns,
                scaler,
                input_window,
                horizon,
                climatology,
            )
            actual_map = {
                pd.Timestamp(r.date).date(): float(r.y)
                for r in group[["date", "y"]].itertuples(index=False)
                if pd.notna(r.y)
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


def aggregate_eval(backtest: pd.DataFrame) -> list[dict[str, Any]]:
    if backtest.empty:
        return []
    out = []
    for lead, group in backtest.groupby("lead_month"):
        m = metrics_for(group["actual"].to_numpy(float), group["predicted"].to_numpy(float))
        out.append({"lead_month": int(lead), **m})
    return out


def lead_uncertainty_spreads(
    *,
    dataset_backtest: pd.DataFrame,
    pooled_backtest: pd.DataFrame,
    history: pd.DataFrame,
    horizon: int,
    quantile: float = 0.90,
) -> dict[int, float]:
    """Return forecast half-widths for each lead month.

    Preference order:
      1) this dataset's backtest residuals for the same lead
      2) pooled source-level residuals for the same lead
      3) a conservative fallback from the historical target spread

    The fallback keeps uncertainty available even when the model has no helper
    variables or too little realized backtest history.
    """

    y = pd.to_numeric(history["y"], errors="coerce").dropna().to_numpy(dtype=float)
    y_std = float(np.nanstd(y)) if len(y) else 1.0
    if not np.isfinite(y_std) or y_std <= 1e-6:
        y_std = 1.0

    def _spread_from(group: pd.DataFrame) -> float | None:
        if group.empty:
            return None
        residual = pd.to_numeric(group["predicted"], errors="coerce") - pd.to_numeric(group["actual"], errors="coerce")
        abs_residual = residual.abs().replace([np.inf, -np.inf], np.nan).dropna()
        if len(abs_residual) >= 8:
            return float(abs_residual.quantile(quantile))
        if len(abs_residual) >= 3:
            rmse = float(math.sqrt(np.mean(np.square(abs_residual.to_numpy(dtype=float)))))
            return 1.64 * rmse
        return None

    spreads: dict[int, float] = {}
    for lead in range(1, horizon + 1):
        ds_group = dataset_backtest[dataset_backtest["lead_month"] == lead] if not dataset_backtest.empty else pd.DataFrame()
        pooled_group = pooled_backtest[pooled_backtest["lead_month"] == lead] if not pooled_backtest.empty else pd.DataFrame()
        spread = _spread_from(ds_group) or _spread_from(pooled_group)
        if spread is None:
            spread = y_std * (0.45 + (0.05 * lead))
        spreads[lead] = max(float(spread), 0.15)
    return spreads


def historical_uncertainty_spreads(*, history: pd.DataFrame, horizon: int) -> dict[int, float]:
    y = pd.to_numeric(history["y"], errors="coerce").dropna().to_numpy(dtype=float)
    y_std = float(np.nanstd(y)) if len(y) else 1.0
    if not np.isfinite(y_std) or y_std <= 1e-6:
        y_std = 1.0
    return {lead: max(float(y_std * (0.45 + (0.05 * lead))), 0.15) for lead in range(1, horizon + 1)}


def rmse_uncertainty_spreads(
    *,
    dataset_backtest: pd.DataFrame,
    pooled_backtest: pd.DataFrame,
    history: pd.DataFrame,
    horizon: int,
) -> dict[int, float]:
    fallback = historical_uncertainty_spreads(history=history, horizon=horizon)

    def _spread_from(group: pd.DataFrame) -> float | None:
        if group.empty:
            return None
        residual = pd.to_numeric(group["predicted"], errors="coerce") - pd.to_numeric(group["actual"], errors="coerce")
        abs_residual = residual.abs().replace([np.inf, -np.inf], np.nan).dropna()
        if len(abs_residual) >= 3:
            rmse = float(math.sqrt(np.mean(np.square(abs_residual.to_numpy(dtype=float)))))
            return max(1.64 * rmse, 0.15)
        return None

    spreads: dict[int, float] = {}
    for lead in range(1, horizon + 1):
        ds_group = dataset_backtest[dataset_backtest["lead_month"] == lead] if not dataset_backtest.empty else pd.DataFrame()
        pooled_group = pooled_backtest[pooled_backtest["lead_month"] == lead] if not pooled_backtest.empty else pd.DataFrame()
        spread = _spread_from(ds_group) or _spread_from(pooled_group) or fallback.get(lead, 0.15)
        spreads[lead] = max(float(spread), 0.15)
    return spreads


def build_interval_payload(
    *,
    value: float | int | None,
    lead_month: int,
    interval_spreads: dict[str, dict[int, float]],
    interval_labels: dict[str, str] | None = None,
) -> dict[str, dict[str, float | str | None]]:
    center = _json_safe_float(value)
    if center is None:
        return {}
    labels = interval_labels or INTERVAL_LABELS
    payload: dict[str, dict[str, float | str | None]] = {}
    for method_name, spreads in interval_spreads.items():
        spread = spreads.get(int(lead_month))
        if spread is None or not np.isfinite(float(spread)):
            continue
        half_width = max(float(spread), 0.15)
        payload[method_name] = {
            "label": labels.get(method_name, method_name.replace("_", " ").title()),
            "lower": center - half_width,
            "upper": center + half_width,
        }
    return payload


def safe_identifier(value: str) -> str:
    raw = str(value or "").strip().lower()
    if not raw or not all(ch.isalnum() or ch == "_" for ch in raw):
        raise ValueError(f"Unsafe SQL identifier: {value!r}")
    return raw


def capture_realized_feedback(conn: Any, *, dataset_key: str, index: str) -> int:
    """Persist old forecast-vs-actual errors before replacing forecasts.

    This is the self-learning memory. Once newly observed months are imported
    into ``ts_<dataset_key>``, any matching old forecast is copied to
    ``prediction_feedback`` with its realized error. The next training run then
    learns from the expanded observed series.
    """

    table = f"ts_{safe_identifier(dataset_key)}"
    idx = safe_identifier(index)
    idx_sql = '"' + idx.replace('"', '') + '"'
    result = conn.execute(
        text(
            f"""
            INSERT INTO prediction_feedback (
              dataset_key, index_name, model_key, feature_id,
              issue_date, target_date, lead_month,
              predicted_value, actual_value, error, absolute_error, squared_error,
              learned_at
            )
            SELECT
              pf.dataset_key,
              pf.index_name,
              dm.model_key,
              pf.feature_id,
              pf.issue_date,
              pf.target_date,
              pf.lead_month,
              pf.value AS predicted_value,
              ts.{idx_sql} AS actual_value,
              pf.value - ts.{idx_sql} AS error,
              ABS(pf.value - ts.{idx_sql}) AS absolute_error,
              POWER(pf.value - ts.{idx_sql}, 2) AS squared_error,
              NOW()
            FROM prediction_forecasts pf
            LEFT JOIN prediction_dataset_models dm
              ON dm.dataset_key = pf.dataset_key
             AND dm.index_name = pf.index_name
            JOIN {table} ts
              ON ts.feature_id = pf.feature_id
             AND ts.date = pf.target_date
            WHERE pf.dataset_key = :k
              AND pf.index_name = :i
              AND pf.value IS NOT NULL
              AND ts.{idx_sql} IS NOT NULL
            ON CONFLICT (dataset_key, index_name, feature_id, issue_date, target_date)
            DO UPDATE SET
              model_key = EXCLUDED.model_key,
              predicted_value = EXCLUDED.predicted_value,
              actual_value = EXCLUDED.actual_value,
              error = EXCLUDED.error,
              absolute_error = EXCLUDED.absolute_error,
              squared_error = EXCLUDED.squared_error,
              learned_at = NOW()
            """
        ),
        {"k": dataset_key, "i": index},
    )
    return int(result.rowcount or 0)


def capture_realized_feedback_for_method(
    conn: Any,
    *,
    dataset_key: str,
    index: str,
    method_name: str,
) -> int:
    table = f"ts_{safe_identifier(dataset_key)}"
    idx = safe_identifier(index)
    idx_sql = '"' + idx.replace('"', '') + '"'
    result = conn.execute(
        text(
            f"""
            INSERT INTO prediction_feedback (
              dataset_key, index_name, method_name, model_key, feature_id,
              issue_date, target_date, lead_month,
              predicted_value, actual_value, error, absolute_error, squared_error,
              learned_at
            )
            SELECT
              pf.dataset_key,
              pf.index_name,
              pf.method_name,
              dm.model_key,
              pf.feature_id,
              pf.issue_date,
              pf.target_date,
              pf.lead_month,
              pf.value AS predicted_value,
              ts.{idx_sql} AS actual_value,
              pf.value - ts.{idx_sql} AS error,
              ABS(pf.value - ts.{idx_sql}) AS absolute_error,
              POWER(pf.value - ts.{idx_sql}, 2) AS squared_error,
              NOW()
            FROM prediction_forecasts pf
            LEFT JOIN prediction_dataset_models dm
              ON dm.dataset_key = pf.dataset_key
             AND dm.index_name = pf.index_name
             AND dm.method_name = pf.method_name
            JOIN {table} ts
              ON ts.feature_id = pf.feature_id
             AND ts.date = pf.target_date
            WHERE pf.dataset_key = :k
              AND pf.index_name = :i
              AND pf.method_name = :m
              AND pf.value IS NOT NULL
              AND ts.{idx_sql} IS NOT NULL
            ON CONFLICT (dataset_key, index_name, method_name, feature_id, issue_date, target_date)
            DO UPDATE SET
              model_key = EXCLUDED.model_key,
              predicted_value = EXCLUDED.predicted_value,
              actual_value = EXCLUDED.actual_value,
              error = EXCLUDED.error,
              absolute_error = EXCLUDED.absolute_error,
              squared_error = EXCLUDED.squared_error,
              learned_at = NOW()
            """
        ),
        {"k": dataset_key, "i": index, "m": method_name},
    )
    return int(result.rowcount or 0)


def write_outputs(
    *,
    method_name: str,
    model_key: str,
    source_key: str,
    index: str,
    input_window: int,
    horizon: int,
    artifact_path: Path,
    feature_columns: list[str],
    training_params: dict[str, Any],
    summary_metrics: dict[str, Any],
    forecasts_by_dataset: dict[str, list[dict[str, Any]]],
    eval_by_dataset: dict[str, list[dict[str, Any]]],
    issue_dates: dict[str, date],
    version_key: str,
    versioned_artifact_path: Path,
) -> None:
    ensure_prediction_tables()
    with engine.begin() as conn:
        conn.execute(
            text(
                """
                INSERT INTO prediction_models (
                  model_key, method_name, source_key, index_name, input_window, horizon, status,
                  artifact_path, feature_columns, training_params, summary_metrics,
                  trained_at, updated_at
                )
                VALUES (
                  :model_key, :method_name, :source_key, :index_name, :input_window, :horizon, 'ready',
                  :artifact_path, CAST(:feature_columns AS JSONB),
                  CAST(:training_params AS JSONB), CAST(:summary_metrics AS JSONB),
                  NOW(), NOW()
                )
                ON CONFLICT (model_key) DO UPDATE
                SET source_key = EXCLUDED.source_key,
                    index_name = EXCLUDED.index_name,
                    input_window = EXCLUDED.input_window,
                    horizon = EXCLUDED.horizon,
                    status = EXCLUDED.status,
                    artifact_path = EXCLUDED.artifact_path,
                    feature_columns = EXCLUDED.feature_columns,
                    training_params = EXCLUDED.training_params,
                    summary_metrics = EXCLUDED.summary_metrics,
                    trained_at = EXCLUDED.trained_at,
                    updated_at = NOW()
                """
            ),
            {
                "model_key": model_key,
                "method_name": method_name,
                "source_key": source_key,
                "index_name": index,
                "input_window": input_window,
                "horizon": horizon,
                "artifact_path": str(artifact_path),
                "feature_columns": json.dumps(feature_columns),
                "training_params": json.dumps(training_params),
                "summary_metrics": json.dumps(summary_metrics),
            },
        )
        conn.execute(
            text(
                """
                INSERT INTO prediction_model_versions (
                  version_key, model_key, method_name, artifact_path, trained_at,
                  feature_columns, training_params, summary_metrics
                )
                VALUES (
                  :version_key, :model_key, :method_name, :artifact_path, NOW(),
                  CAST(:feature_columns AS JSONB),
                  CAST(:training_params AS JSONB),
                  CAST(:summary_metrics AS JSONB)
                )
                ON CONFLICT (version_key) DO UPDATE
                SET artifact_path = EXCLUDED.artifact_path,
                    trained_at = EXCLUDED.trained_at,
                    feature_columns = EXCLUDED.feature_columns,
                    training_params = EXCLUDED.training_params,
                    summary_metrics = EXCLUDED.summary_metrics
                """
            ),
            {
                "version_key": version_key,
                "model_key": model_key,
                "method_name": method_name,
                "artifact_path": str(versioned_artifact_path),
                "feature_columns": json.dumps(feature_columns),
                "training_params": json.dumps(training_params),
                "summary_metrics": json.dumps(summary_metrics),
            },
        )

        for dataset_key, forecast_rows in forecasts_by_dataset.items():
            captured = capture_realized_feedback_for_method(
                conn,
                dataset_key=dataset_key,
                index=index,
                method_name=method_name,
            )
            if captured:
                print(f"[{dataset_key}/{index}/{method_name}] captured realized forecast feedback: {captured:,} rows")
            conn.execute(
                text("DELETE FROM prediction_forecasts WHERE dataset_key = :k AND index_name = :i AND method_name = :m"),
                {"k": dataset_key, "i": index, "m": method_name},
            )
            conn.execute(
                text("DELETE FROM prediction_evaluation WHERE dataset_key = :k AND index_name = :i AND method_name = :m"),
                {"k": dataset_key, "i": index, "m": method_name},
            )
            if forecast_rows:
                forecast_min = min(row["target_date"] for row in forecast_rows)
                forecast_max = max(row["target_date"] for row in forecast_rows)
            else:
                forecast_min = None
                forecast_max = None
            issue_date = issue_dates[dataset_key]
            conn.execute(
                text(
                    """
                    INSERT INTO prediction_dataset_models (
                      dataset_key, index_name, method_name, model_key, issue_date,
                      forecast_min_date, forecast_max_date, updated_at
                    )
                    VALUES (:k, :i, :method_name, :m, :issue, :fmin, :fmax, NOW())
                    ON CONFLICT (dataset_key, index_name, method_name) DO UPDATE
                    SET model_key = EXCLUDED.model_key,
                        issue_date = EXCLUDED.issue_date,
                        forecast_min_date = EXCLUDED.forecast_min_date,
                        forecast_max_date = EXCLUDED.forecast_max_date,
                        updated_at = NOW()
                    """
                ),
                {
                    "k": dataset_key,
                    "i": index,
                    "method_name": method_name,
                    "m": model_key,
                    "issue": issue_date,
                    "fmin": forecast_min,
                    "fmax": forecast_max,
                },
            )
            for row in forecast_rows:
                conn.execute(
                    text(
                        """
                        INSERT INTO prediction_forecasts (
                          dataset_key, index_name, method_name, feature_id, issue_date,
                          target_date, lead_month, value, lower_value, upper_value, intervals,
                          updated_at
                        )
                        VALUES (:k, :i, :m, :fid, :issue, :target, :lead, :value, :lower, :upper, CAST(:intervals AS JSONB), NOW())
                        """
                    ),
                    {
                        "k": dataset_key,
                        "i": index,
                        "m": method_name,
                        "fid": row["feature_id"],
                        "issue": issue_date,
                        "target": row["target_date"],
                        "lead": row["lead_month"],
                        "value": row["value"],
                        "lower": row.get("lower"),
                        "upper": row.get("upper"),
                        "intervals": json.dumps(row.get("intervals") or {}),
                    },
                )
            for metric in eval_by_dataset.get(dataset_key, []):
                conn.execute(
                    text(
                        """
                        INSERT INTO prediction_evaluation (
                          dataset_key, index_name, method_name, lead_month, mae, rmse, bias,
                          r2, correlation, drought_class_accuracy, sample_count,
                          updated_at
                        )
                        VALUES (
                          :k, :i, :m, :lead, :mae, :rmse, :bias,
                          :r2, :corr, :acc, :n, NOW()
                        )
                        """
                    ),
                    {
                        "k": dataset_key,
                        "i": index,
                        "m": method_name,
                        "lead": metric["lead_month"],
                        "mae": metric.get("mae"),
                        "rmse": metric.get("rmse"),
                        "bias": metric.get("bias"),
                        "r2": metric.get("r2"),
                        "corr": metric.get("correlation"),
                        "acc": metric.get("drought_class_accuracy"),
                        "n": metric.get("sample_count"),
                    },
                )


def train_group(
    *,
    source_key: str,
    datasets: list[DatasetInfo],
    index: str,
    feature_root: Path,
    artifact_root: Path,
    input_window: int,
    horizon: int,
    backtest_months: int,
    hidden_size: int,
    dropout: float,
    epochs: int,
    final_epochs: int,
    batch_size: int,
    learning_rate: float,
    use_helpers: bool,
) -> None:
    predictors = load_predictors(feature_root, source_key, use_helpers=use_helpers)
    climatology = predictor_climatology(predictors)
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
            # Keep model inputs consistent across datasets in the pooled model.
            unavailable = [col for col in feature_columns if col not in ds_feature_columns]
            for col in unavailable:
                frame[col] = 0.0
            extra = [col for col in ds_feature_columns if col not in feature_columns and col not in BASE_INPUT_COLUMNS]
            if extra:
                report["helper_columns_dropped"] = sorted(set(report.get("helper_columns_dropped", []) + extra))
                report["helper_columns_used"] = [col for col in report.get("helper_columns_used", []) if col in feature_columns]
        frame["dataset_key"] = ds.key
        frames.append(frame)
        dataset_frames[ds.key] = frame
        feature_reports[ds.key] = report
        print(
            f"[{ds.key}/{index}] input mode={report['mode']}; "
            f"helpers used={len(report['helper_columns_used'])}; "
            f"dropped={len(report['helper_columns_dropped'])}"
        )

    if not frames:
        print(f"[{source_key}/{index}] no trainable datasets")
        return

    combined = pd.concat(frames, ignore_index=True)
    if feature_columns is None:
        print(f"[{source_key}/{index}] no usable feature columns")
        return
    scaler = fit_standardizer(combined, feature_columns)

    max_train_date = pd.Timestamp(combined["date"].max())
    cutoff = max_train_date - pd.DateOffset(months=backtest_months)
    train_x, train_y, _ = make_sequences(combined, feature_columns, scaler, input_window, cutoff=cutoff)
    if len(train_x) < 32:
        print(f"[{source_key}/{index}] too few sequences ({len(train_x)}); skipped")
        return

    print(f"[{source_key}/{index}] training evaluation model on {len(train_x):,} sequences")
    eval_model = train_model(
        train_x,
        train_y,
        hidden_size=hidden_size,
        dropout=dropout,
        epochs=epochs,
        batch_size=batch_size,
        learning_rate=learning_rate,
    )

    eval_by_dataset: dict[str, list[dict[str, Any]]] = {}
    all_eval_rows: list[pd.DataFrame] = []
    for ds in datasets:
        frame = dataset_frames.get(ds.key)
        if frame is None:
            continue
        bt = backtest_recursive(eval_model, frame, feature_columns, scaler, input_window, horizon, climatology, cutoff)
        if not bt.empty:
            bt["dataset_key"] = ds.key
            all_eval_rows.append(bt)
        eval_by_dataset[ds.key] = aggregate_eval(bt)
        print(f"[{ds.key}/{index}] backtest rows={len(bt):,}")

    artifact_root.mkdir(parents=True, exist_ok=True)
    model_key = safe_model_key(source_key, index, METHOD_NAME)
    version_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    version_key = f"{model_key}_{version_stamp}"
    artifact_path = artifact_root / f"{model_key}.pt"
    versioned_artifact_path = artifact_root / f"{version_key}.pt"
    warm_start_model = load_warm_start_model(
        artifact_path=artifact_path,
        feature_columns=feature_columns,
        input_window=input_window,
        horizon=horizon,
        hidden_size=hidden_size,
        dropout=dropout,
    )

    full_x, full_y, _ = make_sequences(combined, feature_columns, scaler, input_window, cutoff=None)
    print(f"[{source_key}/{index}] training final model on {len(full_x):,} sequences")
    final_model = train_model(
        full_x,
        full_y,
        hidden_size=hidden_size,
        dropout=dropout,
        epochs=final_epochs,
        batch_size=batch_size,
        learning_rate=learning_rate,
        initial_model=warm_start_model,
    )

    checkpoint = {
        "model_state": final_model.state_dict(),
        "model_key": model_key,
        "version_key": version_key,
        "source_key": source_key,
        "index": index,
        "input_window": input_window,
        "horizon": horizon,
        "feature_columns": feature_columns,
        "scaler": scaler.to_dict(),
        "hidden_size": hidden_size,
        "dropout": dropout,
        "created_at": version_stamp,
    }
    torch.save(checkpoint, versioned_artifact_path)
    shutil.copyfile(versioned_artifact_path, artifact_path)

    summary_metrics: dict[str, Any] = {}
    pooled_backtest = pd.DataFrame()
    if all_eval_rows:
        pooled_backtest = pd.concat(all_eval_rows, ignore_index=True)
        summary_metrics = metrics_for(pooled_backtest["actual"].to_numpy(float), pooled_backtest["predicted"].to_numpy(float))

    forecasts_by_dataset: dict[str, list[dict[str, Any]]] = {}
    issue_dates: dict[str, date] = {}
    for ds in datasets:
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
        for feature_id, group in frame.groupby("feature_id", sort=False):
            preds = recursive_forecast(final_model, group, feature_columns, scaler, input_window, horizon, climatology)
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
        print(f"[{ds.key}/{index}] forecast rows={len(forecast_rows):,}")

    write_outputs(
        model_key=model_key,
        method_name=METHOD_NAME,
        source_key=source_key,
        index=index,
        input_window=input_window,
        horizon=horizon,
        artifact_path=artifact_path,
        feature_columns=feature_columns,
        training_params={
            "hidden_size": hidden_size,
            "dropout": dropout,
            "epochs": epochs,
            "final_epochs": final_epochs,
            "batch_size": batch_size,
            "learning_rate": learning_rate,
            "backtest_months": backtest_months,
            "adaptive_inputs": {
                "use_helpers": use_helpers,
                "base_columns": list(BASE_INPUT_COLUMNS),
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
                "residual_quantile": 0.90,
                "fallback": "historical target standard deviation scaled by lead",
            },
        },
        summary_metrics=summary_metrics,
        forecasts_by_dataset=forecasts_by_dataset,
        eval_by_dataset=eval_by_dataset,
        issue_dates=issue_dates,
        version_key=version_key,
        versioned_artifact_path=versioned_artifact_path,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Train LSTM+attention drought prediction models")
    parser.add_argument("--dataset", help="Train only one non-station dataset")
    parser.add_argument("--source", help="Train only one source_key")
    parser.add_argument("--index", action="append", help="Index to train, repeatable. Defaults to drought indices.")
    parser.add_argument("--scale", action="append", type=int, help="Train SPI forecast for this scale; repeatable")
    parser.add_argument("--feature-root", default=str(DEFAULT_FEATURE_ROOT))
    parser.add_argument("--artifact-root", default=str(DEFAULT_ARTIFACT_ROOT))
    parser.add_argument("--use-helpers", choices=["auto", "yes", "no"], default="auto")
    parser.add_argument("--input-window", type=int, default=18)
    parser.add_argument("--horizon", type=int, default=12)
    parser.add_argument("--backtest-months", type=int, default=36)
    parser.add_argument("--hidden-size", type=int, default=32)
    parser.add_argument("--dropout", type=float, default=0.15)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--final-epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    args = parser.parse_args()

    torch.set_num_threads(max(1, int(os.getenv("PREDICTION_TORCH_THREADS", "2"))))
    use_helpers = args.use_helpers != "no"
    ensure_prediction_tables()
    datasets = discover_prediction_datasets(args.dataset)
    if args.source:
        datasets = [ds for ds in datasets if ds.source_key == args.source.lower()]
    if not datasets:
        raise SystemExit("No non-station datasets found for prediction training.")

    groups: dict[str, list[DatasetInfo]] = {}
    for ds in datasets:
        groups.setdefault(ds.source_key, []).append(ds)

    for source_key, group in groups.items():
        available = sorted(set.intersection(*(set(get_available_indices(ds.key)) for ds in group)))
        indices = requested_prediction_indices(args.index, args.scale, available)
        for idx in indices:
            if idx not in available:
                print(f"[{source_key}/{idx}] missing in one or more datasets; skipped")
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
                hidden_size=args.hidden_size,
                dropout=args.dropout,
                epochs=args.epochs,
                final_epochs=args.final_epochs,
                batch_size=args.batch_size,
                learning_rate=args.learning_rate,
                use_helpers=use_helpers,
            )


if __name__ == "__main__":
    main()
