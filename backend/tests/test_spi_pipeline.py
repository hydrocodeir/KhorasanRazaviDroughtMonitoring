import numpy as np

from scripts.spi_pipeline.pipeline import Grid, _to_mm


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
