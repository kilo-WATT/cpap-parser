"""MkDocs hook: regenerate docs/device_support.md before each build.

MkDocs discovers this file via the ``hooks:`` key in ``mkdocs.yml``.
No extra plugins required.
"""

from pathlib import Path


def on_pre_build(config) -> None:  # noqa: ANN001
    from cpap_parser.device_profiles import generate_docs

    out = Path(__file__).parent / "device_support.md"
    out.write_text(generate_docs(), encoding="utf-8")
