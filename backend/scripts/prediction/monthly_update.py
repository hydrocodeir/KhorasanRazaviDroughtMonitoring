"""Monthly prediction refresh workflow.

This script is intentionally orchestration-only. It runs the repeatable monthly
steps after new observed data has been prepared:

1) optionally update predictor files
2) optionally import generated datasets
3) train/self-learn prediction models
4) invalidate API cache
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]


def run_step(cmd: list[str], *, dry_run: bool = False) -> None:
    print("+ " + " ".join(cmd), flush=True)
    if dry_run:
        return
    subprocess.run(cmd, cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run monthly prediction update workflow")
    parser.add_argument("--source", help="source_key to refresh, e.g. terraclimate")
    parser.add_argument("--index", action="append", help="Index to train, repeatable")
    parser.add_argument("--scale", action="append", type=int, help="SPI scale to train, repeatable")
    parser.add_argument("--method", action="append", help="Prediction method to train, repeatable")
    parser.add_argument("--dataset", help="Train only one dataset")
    parser.add_argument("--use-helpers", choices=["auto", "yes", "no"], default="auto", help="Whether helper predictors should be used in helper-aware models")
    parser.add_argument(
        "--predictor-input",
        action="append",
        help="Local predictor input file, glob, or directory. Repeatable.",
    )
    parser.add_argument("--enso-file", help="Optional local ENSO CSV/Parquet with date and enso_nino34")
    parser.add_argument("--predictor-config", help="Optional JSON config for helper-specific predictor folders")
    parser.add_argument("--skip-predictors", action="store_true", help="Do not update predictor files")
    parser.add_argument("--skip-import", action="store_true", help="Do not import generated datasets")
    parser.add_argument("--skip-cache-invalidate", action="store_true", help="Do not invalidate API cache")
    parser.add_argument("--dry-run", action="store_true", help="Print commands without running them")
    args = parser.parse_args()

    py = sys.executable

    if not args.skip_predictors:
        if args.source and args.predictor_input:
            predictor_cmd = [py, "-m", "scripts.prediction.download_predictors", "--source", args.source]
            for item in args.predictor_input:
                predictor_cmd += ["--input", item]
            if args.enso_file:
                predictor_cmd += ["--enso-file", args.enso_file]
            if args.predictor_config:
                predictor_cmd += ["--config", args.predictor_config]
            predictor_cmd += ["--use-helpers", "yes" if args.use_helpers != "no" else "no"]
            run_step(predictor_cmd, dry_run=args.dry_run)
        elif args.source and args.predictor_config:
            predictor_cmd = [py, "-m", "scripts.prediction.download_predictors", "--source", args.source, "--config", args.predictor_config]
            predictor_cmd += ["--use-helpers", "yes" if args.use_helpers != "no" else "no"]
            if args.enso_file:
                predictor_cmd += ["--enso-file", args.enso_file]
            run_step(predictor_cmd, dry_run=args.dry_run)
        else:
            print(
                "[monthly-update] predictor refresh skipped. Provide --source together with one or more "
                "--predictor-input values to rebuild monthly predictors from local files.",
                flush=True,
            )

    if not args.skip_import:
        run_step([py, "import_data.py", "--generated-only", "--replace-dataset", "--skip-trends"], dry_run=args.dry_run)

    train_cmd = [py, "-m", "scripts.prediction.train_prediction_models"]
    if args.source:
        train_cmd += ["--source", args.source]
    if args.dataset:
        train_cmd += ["--dataset", args.dataset]
    for idx in args.index or []:
        train_cmd += ["--index", idx]
    for scale in args.scale or []:
        train_cmd += ["--scale", str(max(1, int(scale)))]
    for method in args.method or []:
        train_cmd += ["--method", method]
    train_cmd += ["--use-helpers", args.use_helpers]
    run_step(train_cmd, dry_run=args.dry_run)

    if not args.skip_cache_invalidate:
        # Keep this best-effort so monthly retraining does not fail just because
        # Redis/cache is temporarily unavailable.
        cache_cmd = [
            py,
            "-c",
            "from app.cache import clear_cache; print(clear_cache('api:'))",
        ]
        try:
            run_step(cache_cmd, dry_run=args.dry_run)
        except subprocess.CalledProcessError as exc:
            print(f"[monthly-update] cache invalidation failed: {exc}", flush=True)


if __name__ == "__main__":
    main()
