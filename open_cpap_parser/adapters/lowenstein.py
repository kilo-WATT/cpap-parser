"""
Lowenstein / Weinmann CPAP data adapter.

**DISABLED** — This adapter previously referenced ``cpap-analyst-mcp``.
The DeVilbiss/IntelliPAP Rust extension has replaced the need for
third-party MCP libraries.  If a native Lowenstein parser is needed
in the future, it should be implemented as a Rust/PyO3 extension
derived directly from OSCAR's ``weinmann_loader.cpp``, similar to the
DeVilbiss parser.  The adapter code is retained in git history for
reference but is excluded from the build.
"""
