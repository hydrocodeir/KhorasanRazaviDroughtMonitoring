-- Multi-layer PostGIS schema (created by import_data.py)
--
-- Notes
-- -----
-- - Each imported dataset ("layer") has:
--     * one row in `datasets`
--     * many rows in `features`
--     * one dedicated *wide* time-series table: ts_<dataset_key>
--
-- - Wide tables are used to avoid exploding row counts (CSV contains many SPI/SPEI columns).
--
-- - Spatial index:
--     GiST index on features.geom for fast bbox queries (used by /mapdata)
--
-- - Time indexes:
--     Btree indexes on (date, feature_id) and (feature_id, date) in each ts_<dataset_key>
--     to accelerate map joins and time-series queries.

CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS datasets (
  dataset_key TEXT PRIMARY KEY,
  title TEXT,
  geom_type TEXT,
  min_date DATE,
  max_date DATE,
  metadata JSONB,
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS features (
  dataset_key TEXT NOT NULL REFERENCES datasets(dataset_key) ON DELETE CASCADE,
  feature_id TEXT NOT NULL,
  name TEXT,
  props JSONB,
  geom geometry(Geometry, 4326) NOT NULL,
  min_date DATE,
  max_date DATE,
  PRIMARY KEY (dataset_key, feature_id)
);

CREATE INDEX IF NOT EXISTS idx_features_geom ON features USING gist (geom);
CREATE INDEX IF NOT EXISTS idx_features_name ON features (dataset_key, name);

-- Per-dataset time series tables are named: ts_<dataset_key>
-- They are created dynamically by import_data.py based on the CSV header.
-- Example:
--   CREATE TABLE ts_station (
--     feature_id TEXT NOT NULL,
--     date DATE NOT NULL,
--     spi3 DOUBLE PRECISION,
--     spei3 DOUBLE PRECISION,
--     PRIMARY KEY (feature_id, date)
--   );
--
-- Indexes per ts_<dataset_key>:
--   CREATE INDEX idx_ts_<key>_date_feature ON ts_<key> (date, feature_id);
--   CREATE INDEX idx_ts_<key>_feature_date ON ts_<key> (feature_id, date);

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
);

CREATE TABLE IF NOT EXISTS prediction_dataset_models (
  dataset_key TEXT NOT NULL,
  index_name TEXT NOT NULL,
  model_key TEXT NOT NULL REFERENCES prediction_models(model_key) ON DELETE CASCADE,
  issue_date DATE NOT NULL,
  forecast_min_date DATE,
  forecast_max_date DATE,
  updated_at TIMESTAMPTZ DEFAULT NOW(),
  PRIMARY KEY (dataset_key, index_name)
);

CREATE TABLE IF NOT EXISTS prediction_model_versions (
  version_key TEXT PRIMARY KEY,
  model_key TEXT NOT NULL REFERENCES prediction_models(model_key) ON DELETE CASCADE,
  artifact_path TEXT NOT NULL,
  trained_at TIMESTAMPTZ DEFAULT NOW(),
  feature_columns JSONB,
  training_params JSONB,
  summary_metrics JSONB
);

CREATE INDEX IF NOT EXISTS idx_prediction_model_versions_model
ON prediction_model_versions (model_key, trained_at DESC);

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
);

CREATE INDEX IF NOT EXISTS idx_prediction_forecasts_map
ON prediction_forecasts (dataset_key, index_name, target_date);

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
);

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
);

CREATE INDEX IF NOT EXISTS idx_prediction_feedback_lookup
ON prediction_feedback (dataset_key, index_name, target_date);
