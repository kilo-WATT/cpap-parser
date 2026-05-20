"""Validation report rendering in three formats: Markdown, JSON, and rich terminal.

Each format targets a different consumer:

* **Markdown** — human-readable summary saved to ``validation/reports/``.
* **JSON** — machine/agent-readable structured output for CI integration.
* **Terminal** — coloured rich-text output for interactive development runs.
"""

from __future__ import annotations

import json
from datetime import date as _date
from pathlib import Path
from typing import Optional

from open_cpap_parser.schema import CPAPDirectory
from open_cpap_parser.validation.compare import CompareResult, DayDiff


# ── Markdown ───────────────────────────────────────────────────────────────────

def _fmt(value: Optional[float], precision: int = 2) -> str:
    return f"{value:.{precision}f}" if value is not None else "—"


def _flag(diff: DayDiff, field: str) -> str:
    """Return a warning emoji if the field's delta is out of tolerance."""
    delta = getattr(diff, f"{field}_delta", None)
    tol_map = {
        "ahi": diff.__class__.__module__,  # placeholder; checked below
        "pressure_95": None,
        "usage_hours": None,
        "leak_95": None,
    }
    # Always return "" — caller uses within_tolerance for the row marker.
    return ""


def render_markdown(
    result: CompareResult,
    parsed: CPAPDirectory,
    oscar_count: int,
) -> str:
    """Render a human-readable Markdown validation report.

    Args:
        result: Comparison result from :func:`~compare.compare`.
        parsed: The :class:`CPAPDirectory` from open-cpap-parser.
        oscar_count: Number of days in the OSCAR CSV export.

    Returns:
        Markdown string suitable for saving as a ``.md`` file.
    """
    today = _date.today().isoformat()
    machine = parsed.machine
    tol = result.tolerances
    pct = result.pass_rate * 100
    status = "PASS" if result.passed else "FAIL"

    lines: list[str] = [
        f"# Validation Report — {result.sample_name}",
        f"",
        f"**Date:** {today}  ",
        f"**Device:** {machine.model} ({machine.series}) — serial `{machine.serial_number}`  ",
        f"**Status:** {status} ({pct:.1f}% of matched days within tolerance)  ",
        f"",
        f"## Tolerances",
        f"",
        f"| Metric | Threshold |",
        f"|--------|-----------|",
        f"| AHI | ±{tol.ahi} events/hr |",
        f"| Pressure (95th) | ±{tol.pressure} cmH₂O |",
        f"| Usage | ±{tol.usage_hours} hr |",
        f"| Leak (95th) | ±{tol.leak} L/min |",
        f"",
        f"## Coverage",
        f"",
        f"| | Days |",
        f"|-|------|",
        f"| Parser output | {len(parsed.daily_summaries)} |",
        f"| OSCAR export | {oscar_count} |",
        f"| Matched (both) | {result.total_days} |",
        f"| Within tolerance | {result.days_within_tolerance} |",
    ]

    if result.parser_only_dates:
        lines += [
            f"",
            f"**Parser-only dates** (not in OSCAR export): "
            + ", ".join(result.parser_only_dates),
        ]
    if result.oscar_only_dates:
        lines += [
            f"",
            f"**OSCAR-only dates** (not in parser output): "
            + ", ".join(result.oscar_only_dates),
        ]

    lines += [
        f"",
        f"## Per-day results",
        f"",
        f"| Date | AHI (P) | AHI (O) | ΔAHI | P95 (P) | P95 (O) | ΔP95 | Usage (P) | Usage (O) | ΔUsage | OK |",
        f"|------|---------|---------|------|---------|---------|------|-----------|-----------|--------|-----|",
    ]

    for diff in result.diffs:
        ok = "✓" if diff.within_tolerance else "✗"
        lines.append(
            f"| {diff.date}"
            f" | {_fmt(diff.parser_ahi)}"
            f" | {_fmt(diff.oscar_ahi)}"
            f" | {_fmt(diff.ahi_delta)}"
            f" | {_fmt(diff.parser_pressure_95)}"
            f" | {_fmt(diff.oscar_pressure_95)}"
            f" | {_fmt(diff.pressure_95_delta)}"
            f" | {_fmt(diff.parser_usage_hours)}"
            f" | {_fmt(diff.oscar_usage_hours)}"
            f" | {_fmt(diff.usage_hours_delta)}"
            f" | {ok} |"
        )

    out_of_tol = [d for d in result.diffs if not d.within_tolerance]
    if out_of_tol:
        lines += [
            f"",
            f"## Out-of-tolerance days",
            f"",
        ]
        for diff in out_of_tol:
            lines.append(f"### {diff.date}")
            if diff.ahi_delta is not None:
                lines.append(f"- AHI: parser={_fmt(diff.parser_ahi)}, oscar={_fmt(diff.oscar_ahi)}, Δ={_fmt(diff.ahi_delta)} (tol={tol.ahi})")
            if diff.pressure_95_delta is not None:
                lines.append(f"- Pressure 95th: parser={_fmt(diff.parser_pressure_95)}, oscar={_fmt(diff.oscar_pressure_95)}, Δ={_fmt(diff.pressure_95_delta)} (tol={tol.pressure})")
            if diff.usage_hours_delta is not None:
                lines.append(f"- Usage: parser={_fmt(diff.parser_usage_hours)}, oscar={_fmt(diff.oscar_usage_hours)}, Δ={_fmt(diff.usage_hours_delta)} (tol={tol.usage_hours})")
            if diff.leak_95_delta is not None:
                lines.append(f"- Leak 95th: parser={_fmt(diff.parser_leak_95)}, oscar={_fmt(diff.oscar_leak_95)}, Δ={_fmt(diff.leak_95_delta)} (tol={tol.leak})")

    lines.append("")
    return "\n".join(lines)


# ── JSON ───────────────────────────────────────────────────────────────────────

def render_json(result: CompareResult, parsed: CPAPDirectory, oscar_count: int) -> str:
    """Render a machine-readable JSON validation report.

    Args:
        result: Comparison result from :func:`~compare.compare`.
        parsed: The :class:`CPAPDirectory` from open-cpap-parser.
        oscar_count: Number of days in the OSCAR CSV export.

    Returns:
        JSON string with full comparison data and per-day diffs.
    """
    tol = result.tolerances
    payload = {
        "sample_name": result.sample_name,
        "date": _date.today().isoformat(),
        "status": "PASS" if result.passed else "FAIL",
        "pass_rate": round(result.pass_rate, 4),
        "machine": {
            "serial_number": parsed.machine.serial_number,
            "model": parsed.machine.model,
            "series": parsed.machine.series,
        },
        "tolerances": {
            "ahi": tol.ahi,
            "pressure": tol.pressure,
            "usage_hours": tol.usage_hours,
            "leak": tol.leak,
        },
        "coverage": {
            "parser_days": len(parsed.daily_summaries),
            "oscar_days": oscar_count,
            "matched_days": result.total_days,
            "days_within_tolerance": result.days_within_tolerance,
            "parser_only_dates": result.parser_only_dates,
            "oscar_only_dates": result.oscar_only_dates,
        },
        "days": [
            {
                "date": d.date,
                "within_tolerance": d.within_tolerance,
                "ahi": {"parser": d.parser_ahi, "oscar": d.oscar_ahi, "delta": d.ahi_delta},
                "pressure_95": {"parser": d.parser_pressure_95, "oscar": d.oscar_pressure_95, "delta": d.pressure_95_delta},
                "usage_hours": {"parser": d.parser_usage_hours, "oscar": d.oscar_usage_hours, "delta": d.usage_hours_delta},
                "leak_95": {"parser": d.parser_leak_95, "oscar": d.oscar_leak_95, "delta": d.leak_95_delta},
            }
            for d in result.diffs
        ],
    }
    return json.dumps(payload, indent=2)


# ── Rich terminal ──────────────────────────────────────────────────────────────

def render_terminal(result: CompareResult, parsed: CPAPDirectory, oscar_count: int) -> None:
    """Print a coloured rich-text validation summary to stdout.

    Requires the ``rich`` package (installed via ``pip install open-cpap-parser[validate]``).

    Args:
        result: Comparison result from :func:`~compare.compare`.
        parsed: The :class:`CPAPDirectory` from open-cpap-parser.
        oscar_count: Number of days in the OSCAR CSV export.
    """
    try:
        from rich.console import Console
        from rich.table import Table
        from rich import box
    except ImportError:
        print("Install 'rich' for formatted terminal output: pip install 'open-cpap-parser[validate]'")
        _print_plain(result)
        return

    console = Console()
    tol = result.tolerances
    status_color = "green" if result.passed else "red"
    status_text = "PASS" if result.passed else "FAIL"

    console.print(f"\n[bold]Validation: {result.sample_name}[/bold]")
    console.print(f"  Device:  {parsed.machine.model}  ({parsed.machine.serial_number})")
    console.print(f"  Status:  [{status_color}]{status_text}[/{status_color}]  "
                  f"({result.days_within_tolerance}/{result.total_days} days OK, "
                  f"{result.pass_rate*100:.1f}%)")
    console.print(f"  Days:    parser={len(parsed.daily_summaries)}  oscar={oscar_count}  matched={result.total_days}")

    table = Table(box=box.SIMPLE_HEAVY, show_header=True)
    table.add_column("Date", style="cyan", no_wrap=True)
    table.add_column("AHI (P)", justify="right")
    table.add_column("AHI (O)", justify="right")
    table.add_column("ΔAHI", justify="right")
    table.add_column("P95 (P)", justify="right")
    table.add_column("P95 (O)", justify="right")
    table.add_column("ΔP95", justify="right")
    table.add_column("Usage(P)", justify="right")
    table.add_column("Usage(O)", justify="right")
    table.add_column("OK", justify="center")

    for diff in result.diffs:
        row_style = "" if diff.within_tolerance else "red"
        ok_mark = "[green]✓[/green]" if diff.within_tolerance else "[red]✗[/red]"
        table.add_row(
            diff.date,
            _fmt(diff.parser_ahi),
            _fmt(diff.oscar_ahi),
            _fmt(diff.ahi_delta),
            _fmt(diff.parser_pressure_95),
            _fmt(diff.oscar_pressure_95),
            _fmt(diff.pressure_95_delta),
            _fmt(diff.parser_usage_hours),
            _fmt(diff.oscar_usage_hours),
            ok_mark,
            style=row_style,
        )

    console.print(table)

    if result.parser_only_dates:
        console.print(f"[yellow]Parser-only dates:[/yellow] {', '.join(result.parser_only_dates)}")
    if result.oscar_only_dates:
        console.print(f"[yellow]OSCAR-only dates:[/yellow] {', '.join(result.oscar_only_dates)}")
    console.print()


def _print_plain(result: CompareResult) -> None:
    """Minimal plain-text fallback when rich is not installed."""
    status = "PASS" if result.passed else "FAIL"
    print(f"{result.sample_name}: {status} — {result.days_within_tolerance}/{result.total_days} days within tolerance")
    for diff in result.diffs:
        mark = "OK" if diff.within_tolerance else "FAIL"
        print(f"  {diff.date}: AHI Δ={_fmt(diff.ahi_delta)}  P95 Δ={_fmt(diff.pressure_95_delta)}  Usage Δ={_fmt(diff.usage_hours_delta)}  [{mark}]")


# ── File writer ────────────────────────────────────────────────────────────────

def write_reports(
    result: CompareResult,
    parsed: CPAPDirectory,
    oscar_count: int,
    report_dir: Path,
) -> tuple[Path, Path]:
    """Write Markdown and JSON reports to *report_dir*.

    Args:
        result: Comparison result.
        parsed: Parser output.
        oscar_count: Number of OSCAR CSV days.
        report_dir: Directory to write reports into (created if missing).

    Returns:
        Tuple of ``(markdown_path, json_path)``.
    """
    report_dir.mkdir(parents=True, exist_ok=True)
    today = _date.today().isoformat()
    stem = f"{result.sample_name}-{today}"

    md_path = report_dir / f"{stem}.md"
    json_path = report_dir / f"{stem}.json"

    md_path.write_text(render_markdown(result, parsed, oscar_count), encoding="utf-8")
    json_path.write_text(render_json(result, parsed, oscar_count), encoding="utf-8")

    return md_path, json_path
