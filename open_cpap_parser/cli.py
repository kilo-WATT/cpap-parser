"""Command-line interface for the unified CPAP parser.

Usage::

    cpap-parser --input /path/to/sd_card
    cpap-parser --input /path/to/sd_card --include-timeseries
    cpap-parser --input /path/to/sd_card --include-timeseries --waveform-only
"""

import argparse
import sys
from pathlib import Path

from open_cpap_parser.core import create_parser
from open_cpap_parser.schema import CPAPDirectory


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI argument parser.

    Returns:
        A configured ``ArgumentParser`` instance.
    """
    p = argparse.ArgumentParser(
        prog="cpap-parser",
        description="Parse CPAP SD card directories into unified JSON.",
    )
    p.add_argument(
        "--input",
        "-i",
        type=Path,
        required=True,
        help="Path to CPAP SD card directory",
    )
    p.add_argument(
        "--include-timeseries",
        action="store_true",
        help="Include high-resolution waveform data in output",
    )
    p.add_argument(
        "--waveform-only",
        action="store_true",
        help="Only include daily summaries that have corresponding waveform data",
    )
    return p


def main() -> None:
    """Entry point: parse args, run the parser, print JSON to stdout."""
    parser = build_parser()
    args = parser.parse_args()

    cpap_parser = create_parser()

    try:
        result: CPAPDirectory = cpap_parser.parse(
            directory=args.input,
            include_timeseries=args.include_timeseries,
            waveform_only=args.waveform_only,
        )
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    print(result.model_dump_json(indent=2, exclude_none=True))


if __name__ == "__main__":
    main()
