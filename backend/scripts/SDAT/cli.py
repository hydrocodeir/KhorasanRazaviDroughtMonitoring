"""
Command-line interface for SDAT Python module.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

from .core import sdat_from_matrix, sdat_from_vector
from .io_utils import load_prec_mat, load_precip_txt, load_timeseries_table


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sdat-python",
        description="Compute nonparametric standardized drought indicators (SDAT).",
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)

    vector_parser = subparsers.add_parser(
        "vector", help="Run SDAT for 1D input (numeric txt or tabular csv/txt)."
    )
    vector_parser.add_argument("--input", required=True, help="Path to input file.")
    vector_parser.add_argument("--scale", type=int, default=6, help="Timescale (sc).")
    vector_parser.add_argument(
        "--output",
        default="si_vector.txt",
        help="Output path for SDAT vector values.",
    )
    vector_parser.add_argument(
        "--table",
        action="store_true",
        help="Interpret input as tabular csv/txt with column headers.",
    )
    vector_parser.add_argument(
        "--value-col",
        default="VALUE",
        help="Value column name for table input.",
    )
    vector_parser.add_argument(
        "--date-col",
        default="DATE",
        help="Date column name for table input; use '' to disable sorting by date.",
    )
    vector_parser.add_argument(
        "--id-col",
        default=None,
        help="Optional ID column for selecting one station/grid.",
    )
    vector_parser.add_argument(
        "--id-value",
        default=None,
        help="Optional ID value to filter rows (used with --id-col).",
    )
    vector_parser.add_argument(
        "--delimiter",
        default=None,
        help="Optional delimiter override for table input (e.g., ',' ';' '\\t').",
    )

    matrix_parser = subparsers.add_parser("matrix", help="Run SDAT for 3D MATLAB matrix.")
    matrix_parser.add_argument("--input", required=True, help="Path to input .mat file.")
    matrix_parser.add_argument(
        "--var-name",
        default="prec",
        help="Variable name in .mat file (default: prec).",
    )
    matrix_parser.add_argument("--scale", type=int, default=3, help="Timescale (sc).")
    matrix_parser.add_argument(
        "--output",
        default="si_matrix.npy",
        help="Output path for SDAT matrix values (NumPy .npy).",
    )

    return parser


def run_vector(
    input_path: str,
    scale: int,
    output_path: str,
    table: bool = False,
    value_col: str = "VALUE",
    date_col: str = "DATE",
    id_col: str | None = None,
    id_value: str | None = None,
    delimiter: str | None = None,
) -> None:
    if table:
        date_col_or_none = None if date_col == "" else date_col
        td = load_timeseries_table(
            input_path,
            value_col=value_col,
            date_col=date_col_or_none,
            id_col=id_col,
            id_value=id_value,
            delimiter=delimiter,
        )
    else:
        td = load_precip_txt(input_path)

    si = sdat_from_vector(td, sc=scale)
    np.savetxt(output_path, si, fmt="%.10f")
    print(f"Vector SDAT computed. Output written to: {Path(output_path).resolve()}")


def run_matrix(input_path: str, var_name: str, scale: int, output_path: str) -> None:
    prec = load_prec_mat(input_path, variable_name=var_name)
    si = sdat_from_matrix(prec, sc=scale)
    np.save(output_path, si)
    print(f"Matrix SDAT computed. Output written to: {Path(output_path).resolve()}")


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()

    if args.mode == "vector":
        run_vector(
            args.input,
            args.scale,
            args.output,
            table=args.table,
            value_col=args.value_col,
            date_col=args.date_col,
            id_col=args.id_col,
            id_value=args.id_value,
            delimiter=args.delimiter,
        )
        return

    if args.mode == "matrix":
        run_matrix(args.input, args.var_name, args.scale, args.output)
        return

    raise RuntimeError("Unsupported mode.")


if __name__ == "__main__":
    main()
