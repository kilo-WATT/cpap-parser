"""High-level validation runner: parse SD card → compare against OSCAR → report.

Typical usage from the test suite::

    from cpap_parser.validation.runner import validate_sample

    result = validate_sample(
        sd_card_path=Path("/mnt/sdcard"),
        oscar_csv_path=Path("validation/oscar_exports/cam.csv"),
        sample_name="resmed_cam",
    )
    assert result.pass_rate >= 0.95
"""

from __future__ import annotations

from pathlib import Path

from cpap_parser.schema import CPAPDirectory
from cpap_parser.validation.compare import (
    CompareResult,
    DEFAULT_TOLERANCES,
    Tolerances,
    compare,
)
from cpap_parser.validation.oscar_reader import OscarDaySummary, read_oscar_csv
from cpap_parser.validation.report import render_terminal, write_reports

# Default report output directory (relative to project root when tests run).
_DEFAULT_REPORT_DIR = Path("validation") / "reports"


def _parse_sd_card(sd_card_path: Path) -> CPAPDirectory:
    """Dispatch to the correct manufacturer adapter and return parsed data.

    Args:
        sd_card_path: Root of an SD card dump directory.

    Returns:
        Parsed :class:`~cpap_parser.schema.CPAPDirectory`.

    Raises:
        ValueError: If no adapter recognises the directory.
    """
    from cpap_parser.adapters.lowenstein import LowensteinAdapter
    from cpap_parser.adapters.resmed import ResMedAdapter

    for AdapterClass in (ResMedAdapter, LowensteinAdapter):
        adapter = AdapterClass()
        if adapter.can_handle(sd_card_path):
            return adapter.extract_and_map(sd_card_path)

    raise ValueError(
        f"No adapter recognises {sd_card_path}. "
        "Ensure the directory contains a valid CPAP SD card dump."
    )


def validate_sample(
    sd_card_path: Path,
    oscar_csv_path: Path,
    sample_name: str,
    tolerances: Tolerances = DEFAULT_TOLERANCES,
    report_dir: Path | None = None,
    print_terminal: bool = True,
) -> CompareResult:
    """Parse an SD card, read an OSCAR CSV export, compare, and write reports.

    This is the primary entry point for the pytest validation suite.

    Args:
        sd_card_path: Root directory of the SD card dump to parse.
        oscar_csv_path: Path to the OSCAR CSV export for the same device.
            Export via: OSCAR → File → Export → CSV Export Wizard.
        sample_name: Short label used in report filenames and output headers.
        tolerances: Per-metric deviation thresholds.  Defaults to
            :data:`~compare.DEFAULT_TOLERANCES`.
        report_dir: Directory to write Markdown and JSON reports.  Defaults to
            ``validation/reports/`` relative to the current working directory.
        print_terminal: If ``True``, print a rich-formatted summary to stdout.

    Returns:
        :class:`~compare.CompareResult` with per-day diffs and aggregate stats.

    Raises:
        FileNotFoundError: If *oscar_csv_path* does not exist.
        ValueError: If the SD card directory or CSV cannot be parsed.
    """
    parsed = _parse_sd_card(sd_card_path)
    oscar_summaries: list[OscarDaySummary] = read_oscar_csv(oscar_csv_path)

    result = compare(
        parsed=parsed,
        oscar_summaries=oscar_summaries,
        sample_name=sample_name,
        tolerances=tolerances,
    )

    effective_report_dir = report_dir or _DEFAULT_REPORT_DIR
    md_path, json_path = write_reports(
        result=result,
        parsed=parsed,
        oscar_count=len(oscar_summaries),
        report_dir=effective_report_dir,
    )

    if print_terminal:
        render_terminal(result, parsed, oscar_count=len(oscar_summaries))
        print(f"Reports written to:\n  {md_path}\n  {json_path}")

    return result
