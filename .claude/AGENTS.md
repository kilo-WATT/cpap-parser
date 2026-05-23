# AI Agent Documentation Directives

You are required to treat documentation as a compiled, tested, and strictly maintained artifact. Every code or manuscript change must trigger the following lifecycle.

### Core Workflows

1. **Create:** All new public functions, types, endpoints, and configuration blocks must include inline documentation using the language's native standard.
2. **Maintain:** Whenever modifying existing logic or text, synchronously update the adjacent documentation. Never leave stale docs.
3. **Lint (Auto-Run):** Execute the language's static analysis tool to verify syntax, style, and formatting rules before running tests. Fix any warnings.
4. **Test (Auto-Run):** After modifying documented code, automatically run the language's specific documentation test command to ensure code examples compile and pass.
5. **Compile (Auto-Run):** If the language requires a build step for docs, execute the build command to verify no build-time warnings or broken cross-references occur.

### Language-Specific Execution Matrix

| Language | Creation Standard | Lint Command | Test Command | Auto-Compile Command |
|---|---|---|---|---|
| **Rust** | `rustdoc` (`///`, `//!`) with `# Examples`. | `cargo clippy` | `cargo test --doc` | `RUSTDOCFLAGS="-D warnings" cargo doc --no-deps` |
| **Python** | Google Style Docstrings, `>>>` doctests. | `uv run ruff check` | `pytest --doctest-modules` | `mkdocs build` or `make html` |
