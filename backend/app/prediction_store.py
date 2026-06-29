"""Persistence helpers for drought prediction outputs.

Prediction training is intentionally kept outside the request path. The API
only reads compact model metadata, forecasts, and evaluation metrics that are
written by the backend prediction training scripts.
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

DEFAULT_PREDICTION_METHOD = "lstm_attention"
PREDICTION_METHOD_PRIORITY = {
    "lstm_attention": 0,
    "random_forest": 1,
    "xgboost": 2,
}
PREDICTION_METHOD_LABELS = {
    "lstm_attention": "LSTM + Attention",
    "random_forest": "Random Forest",
    "xgboost": "XGBoost",
}
PREDICTION_INTERVAL_LABELS = {
    "backtest_q90": "Backtest Q90",
    "rmse_164": "RMSE x 1.64",
    "historical_spread": "Historical Spread",
    "sigma_model": "Sigma Model",
}


def normalize_prediction_method(method: str | None) -> str | None:
    if method is None:
        return None
    value = str(method).strip().lower()
    return value or None


def prediction_method_label(method: str | None) -> str:
    key = normalize_prediction_method(method) or DEFAULT_PREDICTION_METHOD
    return PREDICTION_METHOD_LABELS.get(key, key.replace("_", " ").title())


def prediction_interval_label(method: str | None) -> str:
    key = normalize_prediction_method(method) or "backtest_q90"
    return PREDICTION_INTERVAL_LABELS.get(key, key.replace("_", " ").title())


def preferred_prediction_method(methods: list[str]) -> str | None:
    if not methods:
        return None
    ordered = sorted(methods, key=lambda item: (PREDICTION_METHOD_PRIORITY.get(item, 99), item))
    return ordered[0]


def ensure_prediction_tables() -> None:
    """Create persistent prediction tables if needed."""

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS prediction_models (
                  model_key TEXT PRIMARY KEY,
                  method_name TEXT NOT NULL DEFAULT 'lstm_attention',
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
                  method_name TEXT NOT NULL DEFAULT 'lstm_attention',
                  model_key TEXT NOT NULL REFERENCES prediction_models(model_key) ON DELETE CASCADE,
                  issue_date DATE NOT NULL,
                  forecast_min_date DATE,
                  forecast_max_date DATE,
                  updated_at TIMESTAMPTZ DEFAULT NOW(),
                  PRIMARY KEY (dataset_key, index_name, method_name)
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
                  method_name TEXT NOT NULL DEFAULT 'lstm_attention',
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
                  method_name TEXT NOT NULL DEFAULT 'lstm_attention',
                  feature_id TEXT NOT NULL,
                  issue_date DATE NOT NULL,
                  target_date DATE NOT NULL,
                  lead_month INTEGER NOT NULL,
                  value DOUBLE PRECISION,
                  lower_value DOUBLE PRECISION,
                  upper_value DOUBLE PRECISION,
                  intervals JSONB,
                  updated_at TIMESTAMPTZ DEFAULT NOW(),
                  PRIMARY KEY (dataset_key, index_name, method_name, feature_id, target_date)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_prediction_forecasts_map
                ON prediction_forecasts (dataset_key, index_name, method_name, target_date)
                """
            )
        )
        conn.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS prediction_evaluation (
                  dataset_key TEXT NOT NULL,
                  index_name TEXT NOT NULL,
                  method_name TEXT NOT NULL DEFAULT 'lstm_attention',
                  lead_month INTEGER NOT NULL,
                  mae DOUBLE PRECISION,
                  rmse DOUBLE PRECISION,
                  bias DOUBLE PRECISION,
                  r2 DOUBLE PRECISION,
                  correlation DOUBLE PRECISION,
                  drought_class_accuracy DOUBLE PRECISION,
                  sample_count INTEGER,
                  updated_at TIMESTAMPTZ DEFAULT NOW(),
                  PRIMARY KEY (dataset_key, index_name, method_name, lead_month)
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
                  method_name TEXT NOT NULL DEFAULT 'lstm_attention',
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
                  PRIMARY KEY (dataset_key, index_name, method_name, feature_id, issue_date, target_date)
                )
                """
            )
        )
        # Remove deprecated methods so the dashboard only exposes supported
        # prediction methods.
        conn.execute(text("DELETE FROM prediction_feedback WHERE method_name = 'garch'"))
        conn.execute(text("DELETE FROM prediction_feedback WHERE method_name = 'sarimax'"))
        conn.execute(text("DELETE FROM prediction_feedback WHERE method_name = 'elastic_net_ar'"))
        conn.execute(text("DELETE FROM prediction_evaluation WHERE method_name = 'garch'"))
        conn.execute(text("DELETE FROM prediction_evaluation WHERE method_name = 'sarimax'"))
        conn.execute(text("DELETE FROM prediction_evaluation WHERE method_name = 'elastic_net_ar'"))
        conn.execute(text("DELETE FROM prediction_forecasts WHERE method_name = 'garch'"))
        conn.execute(text("DELETE FROM prediction_forecasts WHERE method_name = 'sarimax'"))
        conn.execute(text("DELETE FROM prediction_forecasts WHERE method_name = 'elastic_net_ar'"))
        conn.execute(text("DELETE FROM prediction_dataset_models WHERE method_name = 'garch'"))
        conn.execute(text("DELETE FROM prediction_dataset_models WHERE method_name = 'sarimax'"))
        conn.execute(text("DELETE FROM prediction_dataset_models WHERE method_name = 'elastic_net_ar'"))
        conn.execute(text("DELETE FROM prediction_model_versions WHERE method_name = 'garch' OR model_key LIKE '%_garch' OR version_key LIKE '%_garch_%'"))
        conn.execute(text("DELETE FROM prediction_model_versions WHERE method_name = 'sarimax' OR model_key LIKE '%_sarimax' OR version_key LIKE '%_sarimax_%'"))
        conn.execute(text("DELETE FROM prediction_model_versions WHERE method_name = 'elastic_net_ar' OR model_key LIKE '%_elastic_net_ar' OR version_key LIKE '%_elastic_net_ar_%'"))
        conn.execute(text("DELETE FROM prediction_models WHERE method_name = 'garch' OR model_key LIKE '%_garch'"))
        conn.execute(text("DELETE FROM prediction_models WHERE method_name = 'sarimax' OR model_key LIKE '%_sarimax'"))
        conn.execute(text("DELETE FROM prediction_models WHERE method_name = 'elastic_net_ar' OR model_key LIKE '%_elastic_net_ar'"))
        conn.execute(
            text(
                """
                CREATE INDEX IF NOT EXISTS idx_prediction_feedback_lookup
                ON prediction_feedback (dataset_key, index_name, method_name, target_date)
                """
            )
        )
        for table in (
            "prediction_models",
            "prediction_dataset_models",
            "prediction_model_versions",
            "prediction_forecasts",
            "prediction_evaluation",
            "prediction_feedback",
        ):
            conn.execute(text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS method_name TEXT"))
            conn.execute(
                text(
                    f"""
                    UPDATE {table}
                    SET method_name = :method
                    WHERE method_name IS NULL OR btrim(method_name) = ''
                    """
                ),
                {"method": DEFAULT_PREDICTION_METHOD},
            )
            conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN method_name SET DEFAULT '{DEFAULT_PREDICTION_METHOD}'"))
            conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN method_name SET NOT NULL"))
        conn.execute(text("ALTER TABLE prediction_forecasts ADD COLUMN IF NOT EXISTS intervals JSONB"))
        conn.execute(
            text(
                """
                DO $$
                DECLARE defn TEXT;
                BEGIN
                  SELECT pg_get_constraintdef(oid)
                  INTO defn
                  FROM pg_constraint
                  WHERE conname = 'prediction_dataset_models_pkey';
                  IF defn IS NULL THEN
                    ALTER TABLE prediction_dataset_models
                      ADD CONSTRAINT prediction_dataset_models_pkey
                      PRIMARY KEY (dataset_key, index_name, method_name);
                  ELSIF position('method_name' in defn) = 0 THEN
                    ALTER TABLE prediction_dataset_models DROP CONSTRAINT prediction_dataset_models_pkey;
                    ALTER TABLE prediction_dataset_models
                      ADD CONSTRAINT prediction_dataset_models_pkey
                      PRIMARY KEY (dataset_key, index_name, method_name);
                  END IF;
                EXCEPTION
                  WHEN duplicate_table THEN NULL;
                  WHEN duplicate_object THEN NULL;
                END$$;
                """
            )
        )
        conn.execute(
            text(
                """
                DO $$
                DECLARE defn TEXT;
                BEGIN
                  SELECT pg_get_constraintdef(oid)
                  INTO defn
                  FROM pg_constraint
                  WHERE conname = 'prediction_forecasts_pkey';
                  IF defn IS NULL THEN
                    ALTER TABLE prediction_forecasts
                      ADD CONSTRAINT prediction_forecasts_pkey
                      PRIMARY KEY (dataset_key, index_name, method_name, feature_id, target_date);
                  ELSIF position('method_name' in defn) = 0 THEN
                    ALTER TABLE prediction_forecasts DROP CONSTRAINT prediction_forecasts_pkey;
                    ALTER TABLE prediction_forecasts
                      ADD CONSTRAINT prediction_forecasts_pkey
                      PRIMARY KEY (dataset_key, index_name, method_name, feature_id, target_date);
                  END IF;
                EXCEPTION
                  WHEN duplicate_table THEN NULL;
                  WHEN duplicate_object THEN NULL;
                END$$;
                """
            )
        )
        conn.execute(
            text(
                """
                DO $$
                DECLARE defn TEXT;
                BEGIN
                  SELECT pg_get_constraintdef(oid)
                  INTO defn
                  FROM pg_constraint
                  WHERE conname = 'prediction_evaluation_pkey';
                  IF defn IS NULL THEN
                    ALTER TABLE prediction_evaluation
                      ADD CONSTRAINT prediction_evaluation_pkey
                      PRIMARY KEY (dataset_key, index_name, method_name, lead_month);
                  ELSIF position('method_name' in defn) = 0 THEN
                    ALTER TABLE prediction_evaluation DROP CONSTRAINT prediction_evaluation_pkey;
                    ALTER TABLE prediction_evaluation
                      ADD CONSTRAINT prediction_evaluation_pkey
                      PRIMARY KEY (dataset_key, index_name, method_name, lead_month);
                  END IF;
                EXCEPTION
                  WHEN duplicate_table THEN NULL;
                  WHEN duplicate_object THEN NULL;
                END$$;
                """
            )
        )
        conn.execute(
            text(
                """
                DO $$
                DECLARE defn TEXT;
                BEGIN
                  SELECT pg_get_constraintdef(oid)
                  INTO defn
                  FROM pg_constraint
                  WHERE conname = 'prediction_feedback_pkey';
                  IF defn IS NULL THEN
                    ALTER TABLE prediction_feedback
                      ADD CONSTRAINT prediction_feedback_pkey
                      PRIMARY KEY (dataset_key, index_name, method_name, feature_id, issue_date, target_date);
                  ELSIF position('method_name' in defn) = 0 THEN
                    ALTER TABLE prediction_feedback DROP CONSTRAINT prediction_feedback_pkey;
                    ALTER TABLE prediction_feedback
                      ADD CONSTRAINT prediction_feedback_pkey
                      PRIMARY KEY (dataset_key, index_name, method_name, feature_id, issue_date, target_date);
                  END IF;
                EXCEPTION
                  WHEN duplicate_table THEN NULL;
                  WHEN duplicate_object THEN NULL;
                END$$;
                """
            )
        )


def _method_summary(
    *,
    conn: Any,
    dataset_key: str,
    index: str,
    method: str,
) -> dict[str, Any] | None:
    model_row = conn.execute(
        text(
            """
            SELECT dm.dataset_key, dm.index_name, dm.method_name, dm.model_key, dm.issue_date,
                   dm.forecast_min_date, dm.forecast_max_date,
                   m.source_key, m.input_window, m.horizon, m.status,
                   m.trained_at, m.artifact_path, m.feature_columns,
                   m.training_params, m.summary_metrics
            FROM prediction_dataset_models dm
            JOIN prediction_models m ON m.model_key = dm.model_key
            WHERE dm.dataset_key = :k AND dm.index_name = :i AND dm.method_name = :method
            """
        ),
        {"k": dataset_key, "i": index, "method": method},
    ).fetchone()
    if not model_row:
        return None

    eval_rows = conn.execute(
        text(
            """
            SELECT lead_month, mae, rmse, bias, r2, correlation,
                   drought_class_accuracy, sample_count
            FROM prediction_evaluation
            WHERE dataset_key = :k AND index_name = :i AND method_name = :method
            ORDER BY lead_month
            """
        ),
        {"k": dataset_key, "i": index, "method": method},
    ).fetchall()
    versions_row = conn.execute(
        text(
            """
            SELECT COUNT(*) AS n, MAX(trained_at) AS latest_trained_at
            FROM prediction_model_versions
            WHERE model_key = :m
            """
        ),
        {"m": model_row.model_key},
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
            WHERE dataset_key = :k AND index_name = :i AND method_name = :method
            """
        ),
        {"k": dataset_key, "i": index, "method": method},
    ).fetchone()
    observed_row = conn.execute(
        text(
            f"""
            SELECT MAX(date) AS max_observed
            FROM ts_{_canonical_dataset_key(dataset_key)}
            WHERE "{index}" IS NOT NULL
            """
        )
    ).fetchone()

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
        "dataset_key": dataset_key,
        "index": index,
        "method_name": model_row.method_name,
        "method_label": prediction_method_label(model_row.method_name),
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
        "uncertainty": (model_row.training_params or {}).get("uncertainty", {}),
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


def fetch_prediction_summary(*, dataset_key: str, index: str, method: str | None = None) -> dict[str, Any]:
    """Return model metadata and aggregate evaluation for one dataset/index."""

    key = resolve_dataset_key(dataset_key)
    idx = validate_index_name(dataset_key, index)
    method_name = normalize_prediction_method(method)
    ensure_prediction_tables()

    with engine.begin() as conn:
        method_rows = conn.execute(
            text(
                """
                SELECT DISTINCT dm.method_name
                FROM prediction_dataset_models dm
                WHERE dm.dataset_key = :k AND dm.index_name = :i
                """
            ),
            {"k": key, "i": idx},
        ).fetchall()

        methods = sorted(
            {normalize_prediction_method(row.method_name) or DEFAULT_PREDICTION_METHOD for row in method_rows},
            key=lambda item: (PREDICTION_METHOD_PRIORITY.get(item, 99), item),
        )
        if method_name:
            methods = [item for item in methods if item == method_name]
        summaries = [
            summary
            for item in methods
            if (summary := _method_summary(conn=conn, dataset_key=key, index=idx, method=item)) is not None
        ]

    if not summaries:
        return {
            "available": False,
            "dataset_key": key,
            "index": idx,
            "method_name": method_name,
            "message": "Prediction model has not been trained for this dataset/index yet.",
        }
    chosen_method = method_name or preferred_prediction_method([summary["method_name"] for summary in summaries]) or summaries[0]["method_name"]
    primary = next((summary for summary in summaries if summary["method_name"] == chosen_method), summaries[0]).copy()
    primary["available_methods"] = [summary["method_name"] for summary in summaries]
    primary["methods"] = summaries
    primary["default_method"] = chosen_method
    return primary


def fetch_prediction_forecast(
    *,
    dataset_key: str,
    feature_id: str,
    index: str,
    method: str | None = None,
) -> dict[str, Any]:
    """Return 12-month forecast series for a feature."""

    key = resolve_dataset_key(dataset_key)
    idx = validate_index_name(dataset_key, index)
    method_name = normalize_prediction_method(method)
    ensure_prediction_tables()

    summary = fetch_prediction_summary(dataset_key=key, index=idx, method=method_name)
    if not summary.get("available"):
        return {
            **summary,
            "feature_id": str(feature_id),
            "data": [],
        }

    with engine.begin() as conn:
        methods_payload = []
        for method_summary in summary.get("methods", []):
            rows = conn.execute(
                text(
                    """
                    SELECT target_date, issue_date, lead_month, value, lower_value, upper_value
                         , intervals
                    FROM prediction_forecasts
                    WHERE dataset_key = :k
                      AND index_name = :i
                      AND method_name = :method
                      AND feature_id = :fid
                    ORDER BY target_date
                    """
                ),
                {"k": key, "i": idx, "method": method_summary["method_name"], "fid": str(feature_id)},
            ).fetchall()
            methods_payload.append(
                {
                    **method_summary,
                    "data": [
                        {
                            "date": r.target_date.isoformat(),
                            "issue_date": r.issue_date.isoformat() if r.issue_date else None,
                            "lead_month": int(r.lead_month),
                            "value": _json_safe_float(r.value),
                            "lower": _json_safe_float(r.lower_value),
                            "upper": _json_safe_float(r.upper_value),
                            "intervals": r.intervals or {},
                        }
                        for r in rows
                    ],
                }
            )

    default_method = summary.get("default_method")
    primary_method = next((item for item in methods_payload if item.get("method_name") == default_method), methods_payload[0])
    payload = {**summary, **primary_method}
    payload["feature_id"] = str(feature_id)
    payload["methods"] = methods_payload
    payload["available_methods"] = [item.get("method_name") for item in methods_payload]
    payload["default_method"] = primary_method.get("method_name")
    payload["uncertainty"] = primary_method.get("uncertainty", {})
    payload["available_interval_methods"] = list((primary_method.get("uncertainty", {}) or {}).get("available_interval_methods", []))
    payload["default_interval_method"] = (primary_method.get("uncertainty", {}) or {}).get("default_interval_method")
    return payload


def fetch_prediction_map_values(
    *,
    dataset_key: str,
    index: str,
    yyyymm: str,
    method: str | None = None,
) -> dict[str, float | None]:
    """Return feature_id -> forecast value for one future map month."""

    key = resolve_dataset_key(dataset_key)
    idx = validate_index_name(dataset_key, index)
    month_date = _parse_yyyymm(yyyymm)
    method_name = normalize_prediction_method(method)
    ensure_prediction_tables()
    if method_name is None:
        summary = fetch_prediction_summary(dataset_key=key, index=idx)
        if not summary.get("available"):
            return {}
        method_name = normalize_prediction_method(summary.get("default_method"))
    if method_name is None:
        return {}

    with engine.begin() as conn:
        rows = conn.execute(
            text(
                """
                SELECT feature_id, value
                FROM prediction_forecasts
                WHERE dataset_key = :k
                  AND index_name = :i
                  AND method_name = :method
                  AND target_date = :d
                """
            ),
            {"k": key, "i": idx, "method": method_name, "d": month_date},
        ).fetchall()

    return {str(r.feature_id): _json_safe_float(r.value) for r in rows}


def latest_prediction_max_month(*, dataset_key: str, index: str | None = None, method: str | None = None) -> str | None:
    """Return the latest forecast month for metadata range extension."""

    key = resolve_dataset_key(dataset_key)
    ensure_prediction_tables()
    params: dict[str, Any] = {"k": key}
    where_index = ""
    where_method = ""
    if index:
        idx = validate_index_name(dataset_key, index)
        params["i"] = idx
        where_index = "AND index_name = :i"
    method_name = normalize_prediction_method(method)
    if method_name:
        params["method"] = method_name
        where_method = "AND method_name = :method"

    with engine.begin() as conn:
        row = conn.execute(
            text(
                f"""
                SELECT MAX(forecast_max_date) AS max_d
                FROM prediction_dataset_models
                WHERE dataset_key = :k
                {where_index}
                {where_method}
                """
            ),
            params,
        ).fetchone()

    return row.max_d.strftime("%Y-%m") if row and row.max_d else None


def dataset_has_prediction(*, dataset_key: str, index: str, method: str | None = None) -> bool:
    key = _canonical_dataset_key(dataset_key)
    idx = str(index or "").strip().lower()
    method_name = normalize_prediction_method(method)
    ensure_prediction_tables()
    with engine.begin() as conn:
        if method_name:
            count = conn.execute(
                text(
                    """
                    SELECT COUNT(*)
                    FROM prediction_dataset_models
                    WHERE lower(dataset_key) = :k AND index_name = :i AND method_name = :m
                    """
                ),
                {"k": key, "i": idx, "m": method_name},
            ).scalar_one()
        else:
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
