from pathlib import Path

import pytest

from cpap_parser.adapters.devilbiss import DeVilbissAdapter
from cpap_parser.adapters.base import BaseManufacturerAdapter


@pytest.fixture
def adapter() -> DeVilbissAdapter:
    return DeVilbissAdapter()


class TestDeVilbissCanHandle:
    def test_rejects_empty_directory(self, adapter: BaseManufacturerAdapter, tmp_path: Path):
        assert adapter.can_handle(tmp_path) is False

    def test_rejects_nonexistent_directory(self, adapter: BaseManufacturerAdapter):
        assert adapter.can_handle(Path("/nonexistent")) is False

    def test_detects_dv6_directory(self, adapter: BaseManufacturerAdapter, tmp_path: Path):
        dv6 = tmp_path / "DV6"
        dv6.mkdir()
        (dv6 / "SET.BIN").write_bytes(b"\x00" + b"SN12345678"[:11].ljust(11, b"\x00") + b"\x00" * 141)
        assert adapter.can_handle(tmp_path) is True

    def test_detects_dv5_directory(self, adapter: BaseManufacturerAdapter, tmp_path: Path):
        sl = tmp_path / "SL"
        sl.mkdir()
        (sl / "SET1").write_text("Sn\tSN54321\n")
        assert adapter.can_handle(tmp_path) is True


class TestDeVilbissExtraction:
    def test_extract_dv5_minimal(self, adapter: DeVilbissAdapter, tmp_path: Path):
        import struct
        from datetime import datetime, timezone

        sl = tmp_path / "SL"
        sl.mkdir()
        (sl / "SET1").write_text("Sn\tSN001\nMo\t1\n")

        jan2022 = int(datetime(2022, 1, 1, tzinfo=timezone.utc).timestamp())
        dv5_epoch = jan2022 - 1009843200  # adjust for DeVilbiss epoch offset
        u_data = struct.pack(">IIB", dv5_epoch, dv5_epoch + 28800, 0)
        (sl / "U").write_bytes(u_data)

        result = adapter.extract_and_map(tmp_path)
        assert result.machine.serial_number == "SN001"
        assert len(result.sessions) == 1

    def test_unsupported_directory_raises(self, adapter: DeVilbissAdapter, tmp_path: Path):
        with pytest.raises(ValueError):
            adapter.extract_and_map(tmp_path)
