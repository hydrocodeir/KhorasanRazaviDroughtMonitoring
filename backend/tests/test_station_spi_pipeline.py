import json

from scripts.station_spi_pipeline.pipeline import StationSpiConfig


def test_station_config_supports_multiple_scales_and_dynamic_dataset_key(tmp_path):
    config_path = tmp_path / "station_config.json"
    config_path.write_text(
        json.dumps(
            {
                "input_csv": "stations.csv",
                "output_root": "data/import",
                "dataset_key": "razavi_khorasan_station_spi3",
                "title": "Razavi Khorasan Stations SPI-3 — Stations",
                "scales": [6, 1, 6],
                "source_key": "razavi_khorasan_stations",
                "source_title": "Razavi Khorasan Stations",
                "boundary_key": "station",
                "boundary_title": "Stations",
                "station_id_column": "station_id",
                "station_name_column": "station_name",
                "lon_column": "lon",
                "lat_column": "lat",
                "date_column": "date",
                "precip_column": "rrr24",
            }
        ),
        encoding="utf-8",
    )

    config = StationSpiConfig.load(config_path)
    scaled = config.with_scale(6)

    assert config.scales == (1, 6)
    assert scaled.dataset_key == "razavi_khorasan_station_spi6"
    assert scaled.title == "Razavi Khorasan Stations SPI-6 — Stations"
