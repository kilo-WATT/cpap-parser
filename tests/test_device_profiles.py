"""Tests for the device validation profile registry."""

import warnings
from pathlib import Path

import pytest

from cpap_parser.core import create_parser
from cpap_parser.device_profiles import PROFILES, ValidationStatus, generate_docs, get_profile
from cpap_parser.schema import MachineInfo


# ---------------------------------------------------------------------------
# Profile table integrity
# ---------------------------------------------------------------------------

def test_all_profiles_have_required_keys():
    required = {"manufacturer", "device", "validation_status", "validation_notes"}
    for key, profile in PROFILES.items():
        missing = required - profile.keys()
        assert not missing, f"Profile '{key}' missing keys: {missing}"


def test_all_validation_statuses_are_valid():
    valid = {"validated", "needs_validation", "unimplemented"}
    for key, profile in PROFILES.items():
        assert profile["validation_status"] in valid, (
            f"Profile '{key}' has invalid validation_status '{profile['validation_status']}'"
        )


def test_get_profile_returns_unknown_sentinel_for_missing_key():
    profile = get_profile("does_not_exist")
    assert profile["validation_status"] == "unimplemented"


def test_get_profile_returns_correct_entry():
    profile = get_profile("lowenstein_prisma_line")
    assert profile["validation_status"] == "validated"


# ---------------------------------------------------------------------------
# MachineInfo schema
# ---------------------------------------------------------------------------

def test_machine_info_default_validation_status():
    m = MachineInfo(serial_number="SN001")
    assert m.validation_status == "unimplemented"
    assert m.validation_notes == ""


def test_machine_info_accepts_valid_statuses():
    for status in ("validated", "needs_validation", "unimplemented"):
        m = MachineInfo(serial_number="SN001", validation_status=status)
        assert m.validation_status == status


def test_machine_info_rejects_invalid_status():
    with pytest.raises(Exception):
        MachineInfo(serial_number="SN001", validation_status="experimental")


# ---------------------------------------------------------------------------
# Every registered adapter has a profile entry
# ---------------------------------------------------------------------------

def test_all_registered_adapters_have_profiles():
    """Ensure no adapter ships with profile_key='unknown' that maps to the sentinel."""
    parser = create_parser()
    unknown_adapters = []
    for adapter in parser._adapters:
        key = adapter.profile_key
        if key not in PROFILES:
            unknown_adapters.append(type(adapter).__name__)
    assert not unknown_adapters, (
        f"These adapters have no profile entry in PROFILES: {unknown_adapters}"
    )


# ---------------------------------------------------------------------------
# Warning emission
# ---------------------------------------------------------------------------

def test_needs_validation_emits_warning(tmp_path):
    """parse() must warn when validation_status is needs_validation."""
    resmed_dir = tmp_path / "DATALOG"
    resmed_dir.mkdir()
    parser = create_parser()
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            parser.parse(tmp_path)
        except Exception:
            pass
    validation_warnings = [w for w in caught if issubclass(w.category, UserWarning)
                           and "validation" in str(w.message).lower()]
    assert len(validation_warnings) > 0, "Expected a UserWarning about validation status"


# ---------------------------------------------------------------------------
# docs generation
# ---------------------------------------------------------------------------

def test_generate_docs_contains_all_manufacturers():
    doc = generate_docs()
    for profile in PROFILES.values():
        assert profile["manufacturer"] in doc


def test_generate_docs_contains_status_badges():
    doc = generate_docs()
    assert "✅ validated" in doc
    assert "⚠️ needs validation" in doc
