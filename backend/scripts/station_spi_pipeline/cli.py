from __future__ import annotations

import argparse
import json
import logging

from .pipeline import StationSpiConfig, run_station_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate station-based monthly SPI datasets for the dashboard."
    )
    parser.add_argument("--config", required=True, help="Pipeline JSON configuration")
    parser.add_argument("--scale", action="append", type=int, help="SPI scale in months; repeat to generate several scales")
    parser.add_argument("--discover", action="store_true", help="Print discovered inputs without processing")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    result = run_station_pipeline(
        StationSpiConfig.load(args.config),
        scales={max(1, int(v)) for v in args.scale} if args.scale else None,
        discover_only=args.discover,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
