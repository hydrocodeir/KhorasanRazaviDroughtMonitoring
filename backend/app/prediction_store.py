"""Persistence helpers for LSTM drought prediction outputs.

Prediction training is intentionally kept outside the request path. The API
only reads compact model metadata, forecasts, and evaluation metrics that are
written by ``backend/scripts/prediction/train_lstm_attention.py``.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from sqlalchemy import text

from .database import engine
from .datasets_store import (
    _canonical_dataset_key,
    _json_safe_float,
    _parse_yyyymm,
    resolve_dataset_key,
    validate_index_name,
)


def ensure_prediction_tables() -> None:
    """Create persistent prediction tables if needed."""

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS prediction_models (
                  model_key TEXT PRIMARY KEY,
                  source_key TEXT NOT NULL,
                  index_name TEXT NOT NULL,
                  input_window INTEGER NOT NULL,
                  horizon INTEGER NOT NULL,
                  status TEXT NOT NULL DEFAULT 'ready',
                  trained_at TIMESTAMPTZ DEFAULT NOW(),
                  artifact_path TEXT,
                  feature_columns JSONB,
                  training_params JSONB,
                  summary_metrics JSONB,
                  updated_at TIMESTAMPTZ DEFAULT NOW()
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS prediction_dataset_models (
                  dataset_key TEXT NOT NULL,
                  index_name TEXT NOT NULL,
                  model_key TEXT NOT NULL REFERENCES prediction_models(model_key) ON DELETE CASCADE,
                  issue_date DATE NOT NULL,
                  forecast_min_date DATE,
                  forecast_max_date DATE,
                  updated_at TIMESTAMPTZ DEFAULT NOW(),
                  PRIMARY KEY (dataset_key, index_name)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS prediction_model_versions (
                  version_key TEXT PRIMARY KEY,
                  model_key TEXT NOT NULL REFERENCES prediction_models(model_key) ON DELETE CASCADE,
                  artifact_path TEXT NOT NULL,
                  trained_at TIMESTAMPTZ DEFAULT NOW(),
                  feature_columns JSONB,
                  training_params JSONB,
                  summary_metrics JSONB
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_prediction_model_versions_model
                ON prediction_model_versions (model_key, trained_at DESC)
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS prediction_forecasts (
                  dataset_key TEXT NOT NULL,
                  index_name TEXT NOT NULL,
                  feature_id TEXT NOT NULL,
                  issue_date DATE NOT NULL,
                  target_date DATE NOT NULL,
                  lead_month INTEGER NOT NULL,
                  value DOUBLE PRECISION,
                  lower_value DOUBLE PRECISION,
                  upper_value DOUBLE PRECISION,
                  updated_at TIMESTAMPTZ DEFAULT NOW(),
                  PRIMARY KEY (dataset_key, index_name, feature_id, target_date)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_prediction_forecasts_map
                ON prediction_forecasts (dataset_key, index_name, target_date)
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS prediction_evaluation (
                  dataset_key TEXT NOT NULL,
                  index_name TEXT NOT NULL,
                  lead_month INTEGER NOT NULL,
                  mae DOUBLE PRECISION,
                  rmse DOUBLE PRECISION,
                  bias DOUBLE PRECISION,
                  r2 DOUBLE PRECISION,
                  correlation DOUBLE PRECISION,
                  drought_class_accuracy DOUBLE PRECISION,
                  sample_count INTEGER,
                  updated_at TIMESTAMPTZ DEFAULT NOW(),
                  PRIMARY KEY (dataset_key, index_name, lead_month)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS prediction_feedback (
                  dataset_key TEXT NOT NULL,
                  index_name TEXT NOT NULL,
                  model_key TEXT,
                  feature_id TEXT NOT NULL,
                  issue_date DATE NOT NULL,
                  target_date DATE NOT NULL,
                  lead_month INTEGER NOT NULL,
                  predicted_value DOUBLE PRECISION,
                  actual_value DOUBLE PRECISION,
                  error DOUBLE PRECISION,
                  absolute_error DOUBLE PRECISION,
                  squared_error DOUBLE PRECISION,
                  learned_at TIMESTAMPTZ DEFAULT NOW(),
                  PRIMARY KEY (dataset_key, index_name, feature_id, issue_date, target_date)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_prediction_feedback_lookup
                ON prediction_feedback (dataset_key, index_name, target_date)
                """
            )
        )


def fetch_prediction_summary(*, dataset_key: str, index: str) -> dict[str, Any]:
    """Return model metadata and aggregate evaluation for one dataset/index."""

    key = resolve_dataset_key(dataset_key)
    idx = validate_index_name(dataset_key, index)
    ensure_prediction_tables()

    with engine.begin() as conn:
        model_row = conn.execute(
            text(
                """
                SELECT dm.dataset_key, dm.index_name, dm.model_key, dm.issue_date,
                       dm.forecast_min_date, dm.forecast_max_date,
                       m.source_key, m.input_window, m.horizon, m.status,
                       m.trained_at, m.artifact_path, m.feature_columns,
                       m.training_params, m.summary_metrics
                FROM prediction_dataset_models dm
                JOIN prediction_models m ON m.model_key = dm.model_key
                WHERE dm.dataset_key = :k AND dm.index_name = :i
                """
            ),
            {"k": key, "i": idx},
        ).fetchone()

        eval_rows = conn.execute(
            text(
                """
                SELECT lead_month, mae, rmse, bias, r2, correlation,
                       drought_class_accuracy, sample_count
                FROM prediction_evaluation
                WHERE dataset_key = :k AND index_name = :i
                ORDER BY lead_month
                """
            ),
            {"k": key, "i": idx},
        ).fetchall()
        versions_row = conn.execute(
            text(
                """
                SELECT COUNT(*) AS n, MAX(trained_at) AS latest_trained_at
                FROM prediction_model_versions
                WHERE model_key = :m
                """
            ),
            {"m": model_row.model_key} if model_row else {"m": ""},
        ).fetchone()
        feedback_row = conn.execute(
            text(
                """
                SELECT COUNT(*) AS n,
                       AVG(absolute_error) AS mae,
                       SQRT(AVG(squared_error)) AS rmse,
                       AVG(error) AS bias,
                       MAX(learned_at) AS last_learned_at
                FROM prediction_feedback
                WHERE dataset_key = :k AND index_name = :i
                """
            ),
            {"k": key, "i": idx},
        ).fetchone()
        observed_row = conn.execute(
            text(
                f"""
                SELECT MAX(date) AS max_observed
                FROM ts_{_canonical_dataset_key(key)}
                WHERE "{idx}" IS NOT NULL
                """
            )
        ).fetchone()

    if not model_row:
        return {
            "available": False,
            "dataset_key": key,
            "index": idx,
            "message": "Prediction model has not been trained for this dataset/index yet.",
        }

    metrics = [
        {
            "lead_month": int(r.lead_month),
            "mae": _json_safe_float(r.mae),
            "rmse": _json_safe_float(r.rmse),
            "bias": _json_safe_float(r.bias),
            "r2": _json_safe_float(r.r2),
            "correlation": _json_safe_float(r.correlation),
            "drought_class_accuracy": _json_safe_float(r.drought_class_accuracy),
            "sample_count": int(r.sample_count or 0),
        }
        for r in eval_rows
    ]

    return {
        "available": True,
        "dataset_key": key,
        "index": idx,
        "model_key": model_row.model_key,
        "source_key": model_row.source_key,
        "status": model_row.status,
        "input_window": int(model_row.input_window),
        "horizon": int(model_row.horizon),
        "issue_month": model_row.issue_date.strftime("%Y-%m") if model_row.issue_date else None,
        "forecast_min_month": model_row.forecast_min_date.strftime("%Y-%m") if model_row.forecast_min_date else None,
        "forecast_max_month": model_row.forecast_max_date.strftime("%Y-%m") if model_row.forecast_max_date else None,
        "trained_at": model_row.trained_at.isoformat() if model_row.trained_at else None,
        "artifact_path": model_row.artifact_path,
        "feature_columns": model_row.feature_columns or [],
        "training_params": model_row.training_params or {},
        "summary_metrics": model_row.summary_metrics or {},
        "versioning": {
            "version_count": int(versions_row.n or 0) if versions_row else 0,
            "latest_version_trained_at": versions_row.latest_trained_at.isoformat() if versions_row and versions_row.latest_trained_at else None,
        },
        "freshness": {
            "observed_max_month": observed_row.max_observed.strftime("%Y-%m") if observed_row and observed_row.max_observed else None,
            "issue_month": model_row.issue_date.strftime("%Y-%m") if model_row.issue_date else None,
            "forecast_max_month": model_row.forecast_max_date.strftime("%Y-%m") if model_row.forecast_max_date else None,
            "is_stale": bool(observed_row and observed_row.max_observed and model_row.issue_date and observed_row.max_observed > model_row.issue_date),
        },
        "realized_feedback": {
            "sample_count": int(feedback_row.n or 0) if feedback_row else 0,
            "mae": _json_safe_float(feedback_row.mae) if feedback_row else None,
            "rmse": _json_safe_float(feedback_row.rmse) if feedback_row else None,
            "bias": _json_safe_float(feedback_row.bias) if feedback_row else None,
            "last_learned_at": feedback_row.last_learned_at.isoformat() if feedback_row and feedback_row.last_learned_at else None,
        },
        "evaluation": metrics,
    }


def fetch_prediction_forecast(
    *,
    dataset_key: str,
    feature_id: str,
    index: str,
) -> dict[str, Any]:
    """Return 12-month forecast series for a feature."""

    key = resolve_dataset_key(dataset_key)
    idx = validate_index_name(dataset_key, index)
    ensure_prediction_tables()

    summary = fetch_prediction_summary(dataset_key=key, index=idx)
    if not summary.get("available"):
        return {
            **summary,
            "feature_id": str(feature_id),
            "data": [],
        }

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT target_date, issue_date, lead_month, value, lower_value, upper_value
                FROM prediction_forecasts
                WHERE dataset_key = :k
                  AND index_name = :i
                  AND feature_id = :fid
                ORDER BY target_date
                """
            ),
            {"k": key, "i": idx, "fid": str(feature_id)},
        ).fetchall()

    return {
        **summary,
        "feature_id": str(feature_id),
        "data": [
            {
                "date": r.target_date.isoformat(),
                "issue_date": r.issue_date.isoformat() if r.issue_date else None,
                "lead_month": int(r.lead_month),
                "value": _json_safe_float(r.value),
                "lower": _json_safe_float(r.lower_value),
                "upper": _json_safe_float(r.upper_value),
            }
            for r in rows
        ],
    }


def fetch_prediction_map_values(
    *,
    dataset_key: str,
    index: str,
    yyyymm: str,
) -> dict[str, float | None]:
    """Return feature_id -> forecast value for one future map month."""

    key = resolve_dataset_key(dataset_key)
    idx = validate_index_name(dataset_key, index)
    month_date = _parse_yyyymm(yyyymm)
    ensure_prediction_tables()

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT feature_id, value
                FROM prediction_forecasts
                WHERE dataset_key = :k
                  AND index_name = :i
                  AND target_date = :d
                """
            ),
            {"k": key, "i": idx, "d": month_date},
        ).fetchall()

    return {str(r.feature_id): _json_safe_float(r.value) for r in rows}


def latest_prediction_max_month(*, dataset_key: str, index: str | None = None) -> str | None:
    """Return the latest forecast month for metadata range extension."""

    key = resolve_dataset_key(dataset_key)
    ensure_prediction_tables()
    params: dict[str, Any] = {"k": key}
    where_index = ""
    if index:
        idx = validate_index_name(dataset_key, index)
        params["i"] = idx
        where_index = "AND index_name = :i"

    with engine.begin() as conn:
        row = conn.execute(
            text(
                f"""
                SELECT MAX(forecast_max_date) AS max_d
                FROM prediction_dataset_models
                WHERE dataset_key = :k
                {where_index}
                """
            ),
            params,
        ).fetchone()

    return row.max_d.strftime("%Y-%m") if row and row.max_d else None


def dataset_has_prediction(*, dataset_key: str, index: str) -> bool:
    key = _canonical_dataset_key(dataset_key)
    idx = str(index or "").strip().lower()
    ensure_prediction_tables()
    with engine.begin() as conn:
        count = conn.execute(
            text(
                """
                SELECT COUNT(*)
                FROM prediction_dataset_models
                WHERE lower(dataset_key) = :k AND index_name = :i
                """
            ),
            {"k": key, "i": idx},
        ).scalar_one()
    return bool(count)
