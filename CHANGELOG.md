# Changelog

All notable changes to SpecForge are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses [semantic versioning](https://semver.org/). Prompt versions are tracked separately in `prompts/` and in the README's prompt version history.

## [2.0.0] - 2026-10-08

### Changed (breaking)

- The FRD follows a 21-section enterprise template: document information, sign-off authority, team contacts, revision history, objective, overview (purpose, in scope, out of scope), end-user roles, functional requirements summary, role-based access summary, detailed use cases, role-based access matrix, assumptions and dependencies, status definitions, priority definitions, non-functional, integration and UI requirements, open questions, traceability matrix, acceptance criteria and document sign-off, followed by source-document appendices.
- `frd.json` has a new structure (`info`, `specification`, `access`, `use_cases`, structured `open_questions`; `context.roles` and `context.people` replace `stakeholders`, `assumptions` and `constraints`; `summary` holds the objective, overview and initiative purpose).
- Requirement IDs are sequential (`FR-001`), and priorities use Critical, High, Medium, Low or "To be confirmed" with the source wording in `priority_basis`. A priority is set only when the sources support it.

### Added

- Specification Analyst, Access Analyst and Use Case Writer agents; the pipeline now has ten stages and makes about four more model calls per run.
- Identifiers for role-based access requirements (`RBAR-001`), use cases (`UC-001`), assumptions (`AS-001`), dependencies (`DEP-001`), non-functional requirements (`NFR-001`) and open questions (`OQ-001`).
- Open questions are merged from every agent, including differently worded duplicates, and enriched with gaps: requirements without sources or acceptance criteria, and missing sign-off authorities.
- Traceability status per requirement: Confirmed, Pending clarification, or No source: to be confirmed.
- `--prepared-by` on the CLI and a **Prepared by** field in the web UI.
- The web preview shows the document rendered on the server from the same content as the Word file.
- Runtime prompts: `specification_analyst`, `access_analyst`, `use_case_writer` and the shared `priority_rules` fragment; new versions of the other prompts (see the README's prompt version history).

## [1.2.0] - 2026-10-08

### Added

- Versioned prompt catalog in `prompts/`: one TOML file per prompt version, with its purpose, the code that uses it, its placeholders, expected output and change notes.
- `python -m specforge.prompts` with the `list`, `show`, `history`, `search`, `bump`, `check` and `sync-readme` commands. `check` validates versions, placeholders, change notes, duplicate versions, secret-like strings and README sync.
- Development prompt history: every request that shaped the codebase, as originally written (credentials redacted), with professional rewrites where the original was informal.
- `--version` for the CLI; the version is also reported by the web API.
- `.gitattributes` for consistent line endings; this changelog.
- README sections for installation, configuration, project structure, prompt management with a generated catalog, Git workflow and contribution guidelines.

### Changed

- The agents and the LLM client load prompts from the catalog instead of inline strings. Prompt text is unchanged (version 1.0.0), so results and cached replies are unaffected.
- `.gitignore` now also excludes `.env.*` variants (except `.env.example`), key and credential files, tool caches, logs and editor and OS files.
- Python 3.11 or newer is required (the prompt catalog uses the standard-library `tomllib`).

## [1.1.0] - 2026-10-08

### Added

- Inputs can be a single file, several files, folders or nested subfolders, from the CLI or the web UI. All inputs are normalised into one collection before processing.
- Validation before processing (path exists, readable, valid format, not empty), and a per-file record of relative path, type, size and status (processed, skipped, duplicate, failed) with a reason.
- Deduplication by content (default) or by path, with `--dedup`.
- CLI options `--check` (validate inputs without calling the model), `--chunk-words` and `--chunk-overlap`.
- Web UI folder upload and folder drag-and-drop with relative paths, warnings for skipped files, and a "Files not included" report on the result page.
- Appendix B in the FRD lists files that were not used, and `frd.json` contains the full file report under `files`.

### Changed

- One unreadable file no longer stops a run; the remaining files are processed and the failure is reported.
- Web upload limits raised to 200 files and 200 MB in total (25 MB per file). Uploaded paths are sanitised on the server.

## [1.0.0] - 2026-10-07

### Added

- Seven-agent FRD pipeline: document reader, scope analyst, per-module requirements extractor, coverage sweep, reviewer, refiner and writer.
- Hybrid retrieval (BM25 + bge-small embeddings fused with reciprocal rank fusion) with citation IDs on every passage.
- OpenRouter client with model fallback, exponential backoff, JSON validation and repair, and an on-disk reply cache.
- FastAPI web UI with live progress, a Stop button, an FRD preview, and Word, Markdown and JSON downloads; CLI entry point.

[2.0.0]: ../../compare/v1.2.0...v2.0.0
[1.2.0]: ../../compare/v1.1.0...v1.2.0
[1.1.0]: ../../compare/v1.0.0...v1.1.0
[1.0.0]: ../../releases/tag/v1.0.0
