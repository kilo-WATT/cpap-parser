"""OSCAR cross-validation for the Löwenstein Eyra sample (courtesy @drew2323).

Sample data: https://github.com/drew2323

Prerequisites:
  1. Import ``~/ZedProjects/sleepData/loweinstein-sample/ExampleFiles/`` into OSCAR.
  2. Export a Summary CSV via File → Export → CSV Export Wizard.
     Save to ``~/ZedProjects/sleepData/validation/OSCAR_LowensteinTest_Summary_<Date>.csv``.
  3. Run: ``pytest validation/ -m validation --run-validation -v``

Note: The Löwenstein Prisma Line format is detected via ``config.pcfg``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import pytest

from cpap_parser.validation.compare import DEFAULT_TOLERANCES
from cpap_parser.validation.runner import validate_sample

SAMPLE_NAME = "lowenstein_eyra"
MIN_PASS_RATE = 0.95


@pytest.mark.validation
def test_lowenstein_eyra_accuracy(
    sample_paths: dict[str, Path],
    oscar_csv: Callable[[str], Path],
    report_dir: Path,
) -> None:
    """Validate AHI and usage against OSCAR for the Löwenstein Eyra sample.

    This sample was contributed by @drew2323 (https://github.com/drew2323)
    and covers 6 therapy nights from a Löwenstein Eyra device.

    Asserts that at least 95% of matched nights are within tolerance and that
    no single night has an AHI delta exceeding 0.5 events/hour.
    """
    sd_card = sample_paths[SAMPLE_NAME]
    if not sd_card.is_dir():
        pytest.skip(f"SD card data not found: {sd_card}")

    csv_path = oscar_csv(SAMPLE_NAME)

    result = validate_sample(
        sd_card_path=sd_card,
        oscar_csv_path=csv_path,
        sample_name=SAMPLE_NAME,
        tolerances=DEFAULT_TOLERANCES,
        report_dir=report_dir,
    )

    assert result.total_days > 0, (
        "No dates matched between parser and OSCAR export. "
        "Check that the SD card data and CSV export cover the same date range."
    )

    assert result.pass_rate >= MIN_PASS_RATE, (
        f"Pass rate {result.pass_rate*100:.1f}% < {MIN_PASS_RATE*100:.0f}%. "
        f"See validation/reports/{SAMPLE_NAME}-*.md for details."
    )

    max_ahi_delta = max(
        (d.ahi_delta for d in result.diffs if d.ahi_delta is not None),
        default=0.0,
    )
    assert max_ahi_delta <= 0.5, (
        f"Maximum single-night AHI delta {max_ahi_delta:.2f} exceeds hard ceiling 0.5."
    )
