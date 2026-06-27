from __future__ import annotations

import argparse
import json
import logging

from .pipeline import PipelineConfig, _slug, run_pipeline


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate polygon-based monthly SPI datasets for the dashboard."
    )
    parser.add_argument("--config", required=True, help="Pipeline JSON configuration")
    parser.add_argument("--source", action="append", help="Source key; repeat to select several")
    parser.add_argument("--boundary", action="append", help="Boundary key; repeat to select several")
    parser.add_argument("--scale", action="append", type=int, help="SPI scale in months; repeat to generate several scales")
    parser.add_argument("--discover", action="store_true", help="Print discovered inputs without processing")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(message)s",
    )
    config = PipelineConfig.load(args.config)
    result = run_pipeline(
        config,
        source_keys={_slug(v) for v in args.source} if args.source else None,
        boundary_keys={_slug(v) for v in args.boundary} if args.boundary else None,
        scales={max(1, int(v)) for v in args.scale} if args.scale else None,
        discover_only=args.discover,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
