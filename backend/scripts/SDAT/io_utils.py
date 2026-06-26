"""
I/O helpers for SDAT datasets.
"""

from __future__ import annotations

import csv
from pathlib import Path

import numpy as np


def load_precip_txt(path: str | Path) -> np.ndarray:
    """
    Load 1D text input like SDAT MATLAB/precip.txt.
    """
    return np.loadtxt(path, dtype=float).reshape(-1)


def load_prec_mat(path: str | Path, variable_name: str = "prec") -> np.ndarray:
    """
    Load MATLAB .mat cube (e.g., SDAT_Matrix/prec.mat).

    Requires SciPy: `pip install scipy`
    """
    try:
        from scipy.io import loadmat  # type: ignore
    except Exception as exc:
        raise ImportError(
            "Loading .mat files requires SciPy. Install with: pip install scipy"
        ) from exc

    data = loadmat(path)
    if variable_name not in data:
        raise KeyError(f"Variable '{variable_name}' was not found in {path}.")
    return np.asarray(data[variable_name], dtype=float)


def _detect_delimiter(sample_text: str) -> str:
    """
    Detect table delimiter from text sample.
    """
    try:
        dialect = csv.Sniffer().sniff(sample_text, delimiters=",;\t|")
        return dialect.delimiter
    except Exception:
        # Safe default for CSV files.
        return ","


def load_timeseries_table(
    path: str | Path,
    value_col: str = "VALUE",
    date_col: str | None = "DATE",
    id_col: str | None = None,
    id_value: str | None = None,
    delimiter: str | None = None,
) -> np.ndarray:
    """
    Load a tabular TXT/CSV file and return one 1D value series for SDAT.

    Expected table can include columns such as:
      ID, DATE, VALUE, LAT, LONG, ELEV, ...

    Required:
    - `value_col` must exist.

    Optional:
    - If `id_col` and `id_value` are provided, rows are filtered for one station/grid.
    - If `date_col` is provided and exists, rows are sorted by date string so series is
      time-ordered before SDAT computation.
    """
    path = Path(path)
    sample = path.read_text(encoding="utf-8", errors="ignore")[:4096]
    sep = delimiter if delimiter is not None else _detect_delimiter(sample)

    with path.open("r", encoding="utf-8", errors="ignore", newline="") as f:
        reader = csv.DictReader(f, delimiter=sep)
        if reader.fieldnames is None:
            raise ValueError(
                "Tabular input must have a header row with column names "
                "(e.g., ID,DATE,VALUE,...)"
            )

        fieldnames = [name.strip() for name in reader.fieldnames if name is not None]
        if value_col not in fieldnames:
            raise KeyError(
                f"Value column '{value_col}' not found. Available columns: {fieldnames}"
            )

        rows = []
        for row in reader:
            # Normalize keys by stripping whitespace.
            normalized = {str(k).strip(): v for k, v in row.items() if k is not None}

            if id_col is not None and id_value is not None:
                if id_col not in normalized:
                    raise KeyError(f"ID column '{id_col}' not found in table.")
                if str(normalized[id_col]).strip() != str(id_value):
                    continue

            raw_value = normalized.get(value_col, "")
            if raw_value is None or str(raw_value).strip() == "":
                continue

            try:
                value = float(str(raw_value).strip())
            except ValueError:
                continue

            date_value = None
            if date_col is not None and date_col in normalized:
                date_value = str(normalized[date_col]).strip()

            rows.append((date_value, value))

    if not rows:
        raise ValueError(
            "No usable rows found after applying filters and numeric parsing. "
            "Check column names and ID filter."
        )

    # Sort by date when provided. ISO-like date strings sort correctly lexicographically.
    if date_col is not None:
        rows.sort(key=lambda x: "" if x[0] is None else x[0])

    values = np.array([v for _, v in rows], dtype=float)
    return values
