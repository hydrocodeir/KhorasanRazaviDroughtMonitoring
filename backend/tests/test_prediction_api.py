from __future__ import annotations

from datetime import date
import json
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text

from app.database import engine
from app.main import app
from app.prediction_store import ensure_prediction_tables


client = TestClient(app)


def test_prediction_forecast_api_returns_uncertainty_and_freshness():
    suffix = uuid4().hex[:8]
    dataset_key = f"test_prediction_{suffix}"
    ts_table = f"ts_{dataset_key}"
    model_key = f"{dataset_key}_spi3_lstm_attention"
    version_key = f"{model_key}_20260101T000000Z"

    ensure_prediction_tables()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    """
                    INSERT INTO datasets(dataset_key, title, geom_type, min_date, max_date, metadata)
                    VALUES (:k, :t, 'Point', :min_d, :max_d, CAST(:m AS JSONB))
                    """
                ),
                {
                    "k": dataset_key,
                    "t": "Prediction Test Dataset",
                    "min_d": date(2024, 1, 1),
                    "max_d": date(2024, 2, 1),
                    "m": '{"source_key":"testsource","boundary_key":"test_polygon"}',
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO features(dataset_key, feature_id, name, props, geom, min_date, max_date)
                    VALUES (
                      :k, 'f1', 'Feature 1', '{}'::jsonb,
                      ST_SetSRID(ST_Point(59.0, 36.0), 4326),
                      :min_d, :max_d
                    )
                    """
                ),
                {"k": dataset_key, "min_d": date(2024, 1, 1), "max_d": date(2024, 2, 1)},
            )
            conn.execute(text(f'CREATE TABLE {ts_table} (feature_id TEXT NOT NULL, date DATE NOT NULL, "spi3" DOUBLE PRECISION, PRIMARY KEY(feature_id, date))'))
            conn.execute(
                text(f'INSERT INTO {ts_table}(feature_id, date, "spi3") VALUES (:fid, :d1, :v1), (:fid, :d2, :v2)'),
                {"fid": "f1", "d1": date(2024, 1, 1), "v1": -0.4, "d2": date(2024, 2, 1), "v2": -0.7},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO prediction_models (
                      model_key, source_key, index_name, input_window, horizon,
                      artifact_path, feature_columns, training_params, summary_metrics
                    )
                    VALUES (
                      :m, 'testsource', 'spi3', 18, 12,
                      '/tmp/test.pt', CAST(:features AS JSONB),
                      CAST(:params AS JSONB),
                      CAST(:metrics AS JSONB)
                    )
                    """
                ),
                {
                    "m": model_key,
                    "features": json.dumps(["y", "y_lag_1", "month_sin", "month_cos"]),
                    "params": json.dumps(
                        {
                            "adaptive_inputs": {"dataset_reports": {}},
                            "uncertainty": {
                                "default_interval_method": "backtest_q90",
                                "available_interval_methods": ["backtest_q90", "historical_spread"],
                                "interval_labels": {
                                    "backtest_q90": "Backtest Q90",
                                    "historical_spread": "Historical Spread",
                                },
                            },
                        }
                    ),
                    "metrics": json.dumps({"rmse": 0.4}),
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO prediction_model_versions (
                      version_key, model_key, artifact_path, feature_columns, training_params, summary_metrics
                    )
                    VALUES (:v, :m, '/tmp/test-version.pt', CAST(:features AS JSONB), '{}'::jsonb, '{}'::jsonb)
                    """
                ),
                {"v": version_key, "m": model_key, "features": json.dumps(["y"])},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO prediction_dataset_models (
                      dataset_key, index_name, model_key, issue_date,
                      forecast_min_date, forecast_max_date
                    )
                    VALUES (:k, 'spi3', :m, :issue, :fmin, :fmax)
                    """
                ),
                {
                    "k": dataset_key,
                    "m": model_key,
                    "issue": date(2024, 1, 1),
                    "fmin": date(2024, 3, 1),
                    "fmax": date(2024, 4, 1),
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO prediction_forecasts (
                      dataset_key, index_name, feature_id, issue_date,
                      target_date, lead_month, value, lower_value, upper_value, intervals
                    )
                    VALUES
                      (:k, 'spi3', 'f1', :issue, :d1, 1, -0.9, -1.2, -0.6, CAST(:i1 AS JSONB)),
                      (:k, 'spi3', 'f1', :issue, :d2, 2, -1.1, -1.5, -0.7, CAST(:i2 AS JSONB))
                    """
                ),
                {
                    "k": dataset_key,
                    "issue": date(2024, 1, 1),
                    "d1": date(2024, 3, 1),
                    "d2": date(2024, 4, 1),
                    "i1": json.dumps(
                        {
                            "backtest_q90": {"label": "Backtest Q90", "lower": -1.2, "upper": -0.6},
                            "historical_spread": {"label": "Historical Spread", "lower": -1.05, "upper": -0.75},
                        }
                    ),
                    "i2": json.dumps(
                        {
                            "backtest_q90": {"label": "Backtest Q90", "lower": -1.5, "upper": -0.7},
                            "historical_spread": {"label": "Historical Spread", "lower": -1.3, "upper": -0.9},
                        }
                    ),
                },
            )
            conn.execute(
                text(
                    """
                    INSERT INTO prediction_evaluation (
                      dataset_key, index_name, lead_month, mae, rmse,
                      drought_class_accuracy, sample_count
                    )
                    VALUES (:k, 'spi3', 1, 0.2, 0.3, 0.75, 10)
                    """
                ),
                {"k": dataset_key},
            )

        forecast = client.get(f"/prediction/forecast?level={dataset_key}&index=spi3&region_id=f1")
        assert forecast.status_code == 200
        payload = forecast.json()
        assert payload["available"] is True
        assert payload["default_method"] == "lstm_attention"
        assert payload["available_methods"] == ["lstm_attention"]
        assert payload["data"][0]["lower"] == -1.2
        assert payload["data"][0]["upper"] == -0.6
        assert payload["default_interval_method"] == "backtest_q90"
        assert payload["available_interval_methods"] == ["backtest_q90", "historical_spread"]
        assert payload["data"][0]["intervals"]["historical_spread"]["upper"] == -0.75
        assert payload["versioning"]["version_count"] == 1
        assert payload["freshness"]["is_stale"] is True

        meta = client.get(f"/meta?level={dataset_key}")
        assert meta.status_code == 200
        assert meta.json()["prediction"]["forecast_max_month"] == "2024-04"
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM prediction_forecasts WHERE dataset_key = :k"), {"k": dataset_key})
            conn.execute(text("DELETE FROM prediction_evaluation WHERE dataset_key = :k"), {"k": dataset_key})
            conn.execute(text("DELETE FROM prediction_dataset_models WHERE dataset_key = :k"), {"k": dataset_key})
            conn.execute(text("DELETE FROM prediction_model_versions WHERE model_key = :m"), {"m": model_key})
            conn.execute(text("DELETE FROM prediction_models WHERE model_key = :m"), {"m": model_key})
            conn.execute(text("DELETE FROM features WHERE dataset_key = :k"), {"k": dataset_key})
            conn.execute(text("DELETE FROM datasets WHERE dataset_key = :k"), {"k": dataset_key})
            conn.execute(text(f"DROP TABLE IF EXISTS {ts_table}"))
