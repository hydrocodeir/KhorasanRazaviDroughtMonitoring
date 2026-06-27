import json

import numpy as np

from scripts.spi_pipeline.pipeline import Grid, PipelineConfig, _to_mm


def test_flux_to_daily_millimeters():
    values = np.array([[1e-5, 2e-5]], dtype=float)
    converted = _to_mm(values, "kg m-2 s-1", 86400)
    np.testing.assert_allclose(converted, [[0.864, 1.728]])


def test_grid_extent_uses_cell_edges_for_descending_latitude():
    grid = Grid(
        lat=np.array([1.5, 0.5]),
        lon=np.array([0.5, 1.5]),
        lat_name="lat",
        lon_name="lon",
    )
    assert grid.extent == (0.0, 0.0, 2.0, 2.0)


def test_pipeline_config_supports_multiple_scales(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "boundary_root": "boundaries",
                "output_root": "out",
                "cache_root": "cache",
                "scales": [12, 3, 6, 3],
                "sources": [
                    {
                        "key": "terraclimate",
                        "root": "datasets",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    config = PipelineConfig.load(config_path)

    assert config.scales == (3, 6, 12)
