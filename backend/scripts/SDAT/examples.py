"""
Runnable examples for SDAT Python conversion.
"""

from __future__ import annotations

from pathlib import Path
import csv

import numpy as np

from .core import sdat_from_vector


def run_vector_example() -> np.ndarray:
    """
    Reproduce the SPI.m sample workflow with `precip.txt`.
    """
    repo_root = Path(__file__).resolve().parents[1]
    precip_path = repo_root / "MATLAB" / "SDAT MATLAB" / "precip.txt"
    if precip_path.exists():
        td = np.loadtxt(precip_path, dtype=float)
    else:
        sample_path = repo_root / "app" / "static" / "data" / "Sample.csv"
        with sample_path.open(newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        station_id = rows[0]["ID"]
        td = np.array(
            [float(row["VALUE"]) for row in rows if row["ID"] == station_id],
            dtype=float,
        )
    sc = 6
    si = sdat_from_vector(td, sc=sc)
    return si


if __name__ == "__main__":
    si = run_vector_example()
    print("Computed SDAT vector length:", len(si))
    print("First 20 values:")
    print(np.array2string(si[:20], precision=4, suppress_small=False))
