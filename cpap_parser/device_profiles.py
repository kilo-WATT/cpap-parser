"""Device validation profile registry.

Each entry describes the validation status of a specific parser pipeline.
``validation_status`` reflects the *parser*, not the physical device:

- ``"validated"``       — output has been verified against a reference (e.g. OSCAR)
- ``"needs_validation"``— parser produces output but has not been formally validated
- ``"unimplemented"``   — parser is a stub or absent; output is incomplete

Edit this table as validation improves.  The key strings are matched by
each adapter's ``get_profile_key()`` method and looked up in
``UniversalCPAPParser.parse()`` to stamp ``MachineInfo.validation_status``.
"""

from __future__ import annotations

from typing import Literal

ValidationStatus = Literal["validated", "needs_validation", "unimplemented"]

# ---------------------------------------------------------------------------
# Profile table — one entry per adapter/device combination
# ---------------------------------------------------------------------------

PROFILES: dict[str, dict] = {
    "lowenstein_prisma_line": {
        "manufacturer": "Löwenstein Medical",
        "device": "Prisma Line (prisma25S, prisma25ST)",
        "validation_status": "validated",
        "validation_notes": (
            "Validated against OSCAR v1.7.x: session match >95%, "
            "waveform accuracy baseline established (EPAP ±0.5 hPa)."
        ),
    },
    "lowenstein_eyra": {
        "manufacturer": "Löwenstein Medical",
        "device": "Eyra / Lumis / SOMNOsoft",
        "validation_status": "needs_validation",
        "validation_notes": (
            "Parser implemented; no OSCAR validation performed. "
            "Please consider contributing sample data."
        ),
    },
    "resmed": {
        "manufacturer": "ResMed",
        "device": "AirSense / AirCurve series",
        "validation_status": "needs_validation",
        "validation_notes": (
            "Parser implemented via cpap_py; no OSCAR validation performed. "
            "Please consider contributing sample data."
        ),
    },
    "devilbiss": {
        "manufacturer": "DeVilbiss / IntelliPAP",
        "device": "DV5 / DV6",
        "validation_status": "needs_validation",
        "validation_notes": (
            "Parser implemented; no formal validation performed. "
            "Please consider contributing sample data."
        ),
    },
    "bmc": {
        "manufacturer": "BMC / 3B Medical",
        "device": "G2 / G3 series",
        "validation_status": "needs_validation",
        "validation_notes": (
            "Parser implemented; no formal validation performed. "
            "Please consider contributing sample data."
        ),
    },
    "fisher_paykel": {
        "manufacturer": "Fisher & Paykel",
        "device": "SleepStyle / ICON series",
        "validation_status": "needs_validation",
        "validation_notes": (
            "Parser implemented; no formal validation performed. "
            "Please consider contributing sample data."
        ),
    },
    "yuwell": {
        "manufacturer": "Yuwell / DJMed",
        "device": "BreathCare YH series",
        "validation_status": "needs_validation",
        "validation_notes": (
            "Parser implemented; no formal validation performed. "
            "Please consider contributing sample data."
        ),
    },
    "apex": {
        "manufacturer": "Apex Medical",
        "device": "iCH / XT series",
        "validation_status": "needs_validation",
        "validation_notes": (
            "Parser implemented; no formal validation performed. "
            "Please consider contributing sample data."
        ),
    },
    "respironics": {
        "manufacturer": "Philips Respironics",
        "device": "DreamStation / System One series",
        "validation_status": "needs_validation",
        "validation_notes": (
            "Parser implemented; no formal validation performed. "
            "Please consider contributing sample data."
        ),
    },
}

_UNKNOWN_PROFILE: dict = {
    "manufacturer": "Unknown",
    "device": "Unknown",
    "validation_status": "unimplemented",
    "validation_notes": "No profile registered for this adapter.",
}


def get_profile(key: str) -> dict:
    """Return the profile dict for *key*, or the unknown-profile sentinel."""
    return PROFILES.get(key, _UNKNOWN_PROFILE)


_STATUS_BADGE = {
    "validated": "✅ validated",
    "needs_validation": "⚠️ needs validation",
    "unimplemented": "❌ unimplemented",
}


def generate_docs() -> str:
    """Return a Markdown device-support table built from :data:`PROFILES`.

    Called by the MkDocs hook in ``docs/hooks.py`` before each build so
    the rendered page always reflects the current profile table — no manual
    edits required.
    """
    lines = [
        "# Device Support & Validation Status",
        "",
        "This page is **auto-generated** from `cpap_parser/device_profiles.py`.",
        "Edit the `PROFILES` dict there to update it.",
        "",
        "## Status legend",
        "",
        "| Status | Meaning |",
        "|---|---|",
        "| ✅ validated | Parser output verified against a reference (e.g. OSCAR) |",
        "| ⚠️ needs validation | Parser implemented; not formally validated — "
        "consider [contributing sample data](https://gitlab.com/open-cpap/cpap-parser/-/issues) |",
        "| ❌ unimplemented | Parser is a stub or absent |",
        "",
        "## Supported devices",
        "",
        "| Manufacturer | Device | Status | Notes |",
        "|---|---|---|---|",
    ]
    for profile in PROFILES.values():
        badge = _STATUS_BADGE.get(profile["validation_status"], profile["validation_status"])
        notes = profile.get("validation_notes", "").replace("\n", " ")
        lines.append(
            f"| {profile['manufacturer']} | {profile['device']} | {badge} | {notes} |"
        )
    lines.append("")
    return "\n".join(lines)
