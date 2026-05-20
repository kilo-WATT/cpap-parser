"""pytest configuration for the OSCAR validation suite.

Tests in this suite are gated behind ``--run-validation``.  They require:

1. OSCAR installed (``/usr/bin/OSCAR`` or ``flatpak run com.sleepfiles.OSCAR``).
2. SD card data imported into OSCAR for each sample.
3. OSCAR CSV exports in ``~/ZedProjects/sleepData/validation/`` following the
   naming convention ``OSCAR_<Profile>_Summary_<Date>.csv``.
4. SD card dumps available at paths configured in the ``sample_paths`` fixture.

Run the suite::

    pytest validation/ -m validation --run-validation -v

Override the OSCAR export directory with the ``OSCAR_EXPORT_ROOT`` env var.
Override the SD card root with the ``SLEEP_DATA_ROOT`` env var.
"""

from __future__ import annotations

import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--run-validation",
        action="store_true",
        default=False,
        help="Run OSCAR cross-validation tests (requires SD card data and OSCAR CSV exports).",
    )


def pytest_collection_modifyitems(
    config: pytest.Config,
    items: list[pytest.Item],
) -> None:
    if config.getoption("--run-validation"):
        return
    skip = pytest.mark.skip(reason="Pass --run-validation to run OSCAR validation tests.")
    for item in items:
        if "validation" in item.keywords:
            item.add_marker(skip)


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def sleep_data_root() -> Path:
    """Root of the local CPAP test corpus (~/ZedProjects/sleepData by default).

    Override by setting the ``SLEEP_DATA_ROOT`` environment variable.
    """
    import os
    env = os.environ.get("SLEEP_DATA_ROOT")
    if env:
        return Path(env)
    return Path.home() / "ZedProjects" / "sleepData"


@pytest.fixture(scope="session")
def sample_paths(sleep_data_root: Path) -> dict[str, Path]:
    """Map of sample name → SD card dump root directory.

    Keys:

    * ``resmed_cam`` — Camden's AirSense 11 AutoSet (72 nights)
    * ``resmed_hanna`` — Hanna's AirSense 11 AutoSet (45 nights)
    * ``lowenstein_eyra`` — Löwenstein Eyra sample from @drew2323
    """
    return {
        "resmed_cam": sleep_data_root / "tmpdata" / "cam",
        "resmed_hanna": sleep_data_root / "tmpdata" / "hanna",
        "lowenstein_eyra": sleep_data_root / "loweinstein-sample" / "ExampleFiles",
    }


_OSCAR_EXPORT_ROOT_DEFAULT = Path.home() / "ZedProjects" / "sleepData" / "validation"
_OSCAR_DATA_ROOT = Path.home() / "Documents" / "OSCAR_Data"

# Maps sample name → OSCAR profile prefix used in export filenames.
_OSCAR_PROFILE_NAMES: dict[str, str] = {
    "resmed_cam":      "ResMedCam",
    "resmed_hanna":    "ResMedHanna",
    "lowenstein_eyra": "LowensteinTest",
}

# Maps sample name → OSCAR profile directory name (used by oscar-export CLI).
_OSCAR_PROFILE_DIRS: dict[str, str] = {
    "resmed_cam":      "ResMedTest1",
    "resmed_hanna":    "ResMedTest2",
    "lowenstein_eyra": "LowensteinTest",
}


@pytest.fixture(scope="session")
def oscar_export_root() -> Path:
    """Root directory for OSCAR CSV exports.

    Defaults to ``~/ZedProjects/sleepData/validation/``.
    Override with the ``OSCAR_EXPORT_ROOT`` environment variable.
    """
    import os
    env = os.environ.get("OSCAR_EXPORT_ROOT")
    return Path(env) if env else _OSCAR_EXPORT_ROOT_DEFAULT


@pytest.fixture(scope="session")
def oscar_csv(oscar_export_root: Path):
    """Return a callable that resolves sample name → OSCAR Summary CSV path.

    Searches *oscar_export_root* for files matching
    ``OSCAR_<Profile>_Summary_*.csv`` and returns the most recent match
    (sorted by filename, which encodes the export date).

    Calling the returned function with an unknown sample name or when no
    matching file exists causes the test to skip automatically.

    Example::

        def test_foo(oscar_csv):
            csv_path = oscar_csv("resmed_cam")
    """
    def _find(sample_name: str) -> Path:
        profile = _OSCAR_PROFILE_NAMES.get(sample_name)
        if not profile:
            pytest.skip(f"No OSCAR profile name configured for sample '{sample_name}'.")
        pattern = f"OSCAR_{profile}_Summary_*.csv"
        matches = sorted(oscar_export_root.glob(pattern))
        if not matches:
            pytest.skip(
                f"No OSCAR Summary CSV found for '{sample_name}'. "
                f"Expected pattern: {oscar_export_root / pattern}"
            )
        return matches[-1]

    return _find


@pytest.fixture(scope="session")
def report_dir() -> Path:
    """Directory where Markdown and JSON reports are written."""
    p = Path(__file__).parent / "reports"
    p.mkdir(exist_ok=True)
    return p


_OSCAR_EXPORT_SRC = Path.home() / "ZedProjects" / "oscar-export"


def _oscar_export_cmd() -> list[str] | None:
    """Return the command prefix to invoke oscar-export, or None if unavailable.

    Preference order:
    1. Local patched source at ~/ZedProjects/oscar-export/ (go run .)
    2. Installed binary on PATH or ~/go/bin/oscar-export
    """
    if (_OSCAR_EXPORT_SRC / "main.go").is_file() and shutil.which("go"):
        return ["go", "run", "."]
    for candidate in (
        shutil.which("oscar-export"),
        str(Path.home() / "go" / "bin" / "oscar-export"),
    ):
        if candidate and Path(candidate).is_file():
            return [candidate]
    return None


@pytest.fixture(scope="session", autouse=True)
def _refresh_oscar_exports(request, oscar_export_root: Path) -> None:
    """Run oscar-export to generate fresh Summary CSVs before validation tests.

    Uses the locally patched oscar-export source at ~/ZedProjects/oscar-export/
    (run via ``go run .``), falling back to any installed binary.  If neither
    is available, existing CSV exports are used as-is.

    Skips export for a sample if today's file already exists.
    """
    if not request.config.getoption("--run-validation"):
        return

    cmd_prefix = _oscar_export_cmd()
    if cmd_prefix is None:
        print(
            "\nWARNING: oscar-export not found — using existing CSV exports.\n"
            "Source: ~/ZedProjects/oscar-export/ (requires Go)\n"
        )
        return

    oscar_export_root.mkdir(parents=True, exist_ok=True)
    today = date.today().isoformat()
    cwd = str(_OSCAR_EXPORT_SRC) if cmd_prefix[0] == "go" else None

    for sample_name, profile_dir in _OSCAR_PROFILE_DIRS.items():
        display = _OSCAR_PROFILE_NAMES[sample_name]
        out_path = oscar_export_root / f"OSCAR_{display}_Summary_{today}.csv"
        if out_path.exists():
            continue

        cmd = cmd_prefix + [
            "export", "summary",
            "--root", str(_OSCAR_DATA_ROOT),
            "--profile-user", profile_dir,
            "--from", "2020-01-01",
            "--to", today,
            "--out", str(out_path),
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
        if result.returncode != 0:
            print(f"\nWARNING: oscar-export failed for {sample_name}:\n{result.stderr}\n")
