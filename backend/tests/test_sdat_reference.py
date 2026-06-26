import numpy as np
import pandas as pd

from scripts.SDAT.core import sdat_from_vector, sdat_from_vector_reference


def test_reference_full_period_matches_original_sdat():
    rng = np.random.default_rng(42)
    values = rng.gamma(shape=2.0, scale=20.0, size=40 * 12)
    dates = pd.date_range("1981-01-01", periods=len(values), freq="MS")

    expected = sdat_from_vector(values, sc=3)
    actual = sdat_from_vector_reference(
        values,
        dates,
        sc=3,
        minimum_reference_years=1,
    )
    np.testing.assert_allclose(actual, expected, equal_nan=True)


def test_reference_period_requires_enough_calendar_month_samples():
    values = np.arange(10 * 12, dtype=float) + 1
    dates = pd.date_range("2000-01-01", periods=len(values), freq="MS")
    actual = sdat_from_vector_reference(
        values,
        dates,
        sc=3,
        reference_start="2000-01",
        reference_end="2004-12",
        minimum_reference_years=10,
    )
    assert np.isnan(actual).all()


def test_missing_month_only_invalidates_overlapping_rolling_windows():
    rng = np.random.default_rng(7)
    values = rng.gamma(shape=2.0, scale=10.0, size=30 * 12)
    dates = pd.date_range("1991-01-01", periods=len(values), freq="MS")
    values[100] = np.nan

    actual = sdat_from_vector_reference(
        values,
        dates,
        sc=3,
        minimum_reference_years=20,
    )

    assert np.isnan(actual[100:103]).all()
    assert np.isfinite(actual[99])
    assert np.isfinite(actual[103])
