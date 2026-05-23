"""Dev-only validation module for cross-checking open-cpap-parser against OSCAR.

This package is **not** included in the published wheel.  It is intended for
developers who want to verify parser accuracy against OSCAR's reference output.

Workflow:

1. Open OSCAR and import the SD card data (File → Import CPAP Data).
2. Export a per-day CSV summary (File → Export → CSV Export Wizard) and save it
   to ``validation/oscar_exports/<sample_name>.csv``.
3. Run the validation suite::

       pytest validation/ -m validation --run-validation -v

See ``validation/README.md`` for full setup instructions.
"""
