"""Train one or more drought prediction methods through a single CLI."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
METHOD_TO_MODULE = {
    "lstm_attention": "scripts.prediction.train_lstm_attention",
    "random_forest": "scripts.prediction.train_random_forest",
    "xgboost": "scripts.prediction.train_xgboost",
}


def append_repeatable(cmd: list[str], flag: str, values: list[str] | None) -> None:
    for value in values or []:
        cmd.extend([flag, str(value)])


def main() -> None:
    parser = argparse.ArgumentParser(description="Train one or more drought prediction methods")
    parser.add_argument("--dataset", help="Train only one non-station dataset")
    parser.add_argument("--source", help="Train only one source_key")
    parser.add_argument("--index", action="append", help="Index to train, repeatable")
    parser.add_argument("--scale", action="append", type=int, help="SPI scale to train, repeatable")
    parser.add_argument("--method", action="append", choices=sorted(METHOD_TO_MODULE), help="Prediction method to train")
    parser.add_argument("--feature-root", help="Root folder containing helper predictor parquet files")
    parser.add_argument("--artifact-root", help="Root folder for trained model artifacts")
    parser.add_argument("--input-window", type=int, default=18)
    parser.add_argument("--horizon", type=int, default=12)
    parser.add_argument("--backtest-months", type=int, default=36)
    parser.add_argument("--use-helpers", choices=["auto", "yes", "no"], default="auto")
    parser.add_argument("--hidden-size", type=int, default=32)
    parser.add_argument("--dropout", type=float, default=0.15)
    parser.add_argument("--epochs", type=int, default=30)
    parser.add_argument("--final-epochs", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=128)
    parser.add_argument("--learning-rate", type=float, default=1e-3)
    parser.add_argument("--rf-trees", type=int, default=300)
    parser.add_argument("--rf-max-depth", type=int, default=10)
    parser.add_argument("--rf-min-samples-leaf", type=int, default=2)
    parser.add_argument("--xgb-trees", type=int, default=400)
    parser.add_argument("--xgb-max-depth", type=int, default=6)
    parser.add_argument("--xgb-learning-rate", type=float, default=0.05)
    parser.add_argument("--xgb-subsample", type=float, default=0.9)
    parser.add_argument("--xgb-colsample-bytree", type=float, default=0.9)
    args = parser.parse_args()

    methods = args.method or ["lstm_attention"]
    py = sys.executable

    for method in methods:
        module = METHOD_TO_MODULE[method]
        cmd = [py, "-m", module]
        if args.dataset:
            cmd.extend(["--dataset", args.dataset])
        if args.source:
            cmd.extend(["--source", args.source])
        append_repeatable(cmd, "--index", args.index)
        append_repeatable(cmd, "--scale", [str(item) for item in args.scale or []])
        if args.artifact_root:
            cmd.extend(["--artifact-root", args.artifact_root])
        cmd.extend(["--input-window", str(args.input_window), "--horizon", str(args.horizon), "--backtest-months", str(args.backtest_months)])

        if method in {"lstm_attention", "random_forest"}:
            if args.feature_root:
                cmd.extend(["--feature-root", args.feature_root])
            cmd.extend(["--use-helpers", args.use_helpers])
        if method == "lstm_attention":
            cmd.extend(
                [
                    "--hidden-size",
                    str(args.hidden_size),
                    "--dropout",
                    str(args.dropout),
                    "--epochs",
                    str(args.epochs),
                    "--final-epochs",
                    str(args.final_epochs),
                    "--batch-size",
                    str(args.batch_size),
                    "--learning-rate",
                    str(args.learning_rate),
                ]
            )
        elif method == "random_forest":
            cmd.extend(
                [
                    "--rf-trees",
                    str(args.rf_trees),
                    "--rf-max-depth",
                    str(args.rf_max_depth),
                    "--rf-min-samples-leaf",
                    str(args.rf_min_samples_leaf),
                ]
            )
        elif method == "xgboost":
            cmd.extend(
                [
                    "--xgb-trees",
                    str(args.xgb_trees),
                    "--xgb-max-depth",
                    str(args.xgb_max_depth),
                    "--xgb-learning-rate",
                    str(args.xgb_learning_rate),
                    "--xgb-subsample",
                    str(args.xgb_subsample),
                    "--xgb-colsample-bytree",
                    str(args.xgb_colsample_bytree),
                ]
            )
        print("+ " + " ".join(cmd), flush=True)
        subprocess.run(cmd, cwd=ROOT, check=True)


if __name__ == "__main__":
    main()
