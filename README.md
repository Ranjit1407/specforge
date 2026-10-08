# SpecForge

Generates a Functional Requirements Document (FRD) from any set of source documents (BRDs, meeting notes, emails, specs, RFPs) using a chain of LLM agents over a hybrid RAG index. It is domain-agnostic: modules, user roles, search queries and requirements all come from the documents you upload. Runs on free models through OpenRouter.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # then put your OpenRouter key in .env

.\.venv\Scripts\python -m specforge.web     # web UI at http://127.0.0.1:8000
```

In the web UI, drop in files or whole folders (or use **Select a folder**), optionally name the project, and click **Generate FRD**. You can watch each agent's progress live, then preview the FRD and download it as Word, Markdown or JSON.

### Inputs

SpecForge accepts a single file, several files, a folder, nested subfolders, or any mix of these. The web UI and the CLI feed the same pipeline, so the same inputs give the same results.

```powershell
.\.venv\Scripts\python -m specforge brd.pdf                                   # one file
.\.venv\Scripts\python -m specforge brd.pdf notes.txt minutes.docx             # several files
.\.venv\Scripts\python -m specforge project-docs\ -o output\billing           # a folder, scanned recursively
.\.venv\Scripts\python -m specforge project-docs\ extra.pdf --title "Billing"  # a mix
.\.venv\Scripts\python -m specforge project-docs\ --check                      # validate and list inputs only, no model calls
```

| Option | Meaning |
| --- | --- |
| `-o, --out DIR` | Output folder (default `output`) |
| `--title NAME` | Project name for the FRD title |
| `--check` | Validate and read every input, print a per-file table, and exit without calling the model |
| `--dedup content\|path` | `content` (default): files with identical content are processed once. `path`: only the same file reached twice is |
| `--chunk-words N`, `--chunk-overlap N` | Passage size and overlap in words (defaults 220 and 40) |
| `--top-k N` | Passages retrieved per search query (default 6) |
| `--no-dense` | Keyword (BM25) retrieval only; skips the embedding model download |
| `--no-cache` | Ignore cached model replies |

Every file found gets a record with its relative path (for example `project-docs/specs/api.txt`), type, size and status:

- **processed**: read and indexed. It gets a reference such as `D3` that requirements cite.
- **skipped**: unsupported type (supported: `.pdf`, `.docx`, `.md`, `.txt`). It is ignored with a warning.
- **duplicate**: same content as another file, or the same file listed twice.
- **failed**: could not be used (empty, unreadable, corrupt, password-protected, or a PDF with no text layer). The rest of the files are still processed.

Hidden files and folders (`.git`, `.DS_Store`) and Office lock files (`~$name.docx`) are ignored. A path that does not exist stops the run before anything is processed. The per-file results appear in the CLI output, on the result page, in the FRD's appendices (Appendix B lists files not included) and in `frd.json` under `files`. The run fails only when no file at all can be read.

## How it works

```
documents ─► chunk ─► hybrid index (BM25 + bge-small embeddings, fused with RRF)
                              │
   ┌──────────────────────────┴────────────────────────────────────────────┐
   │ 1 Document Reader    small sets: whole text passed on as-is;          │
   │                      large sets: summarised part by part, with IDs    │
   │ 2 Scope Analyst      → title, purpose, scope, roles, constraints,     │
   │                        modules + search queries in the docs' terms    │
   │ 3 Req. Extractor     per module: retrieve → requirements with         │
   │                      acceptance criteria + citations                  │
   │ 4 Coverage Sweep     excerpts no requirement cites → missed reqs      │
   │ 5 Reviewer           duplicates, conflicts, ambiguity, open questions │
   │ 6 Refiner            flagged reqs + their cited evidence → rewritten  │
   │ 7 Writer             executive summary + module overviews            │
   └────────────────────────────────────────────────────────────────────────┘
                              │
                FRD.md · FRD.docx · frd.json · steps/*.json
```

Techniques used:

- **Understand first, then retrieve.** The Scope Analyst works from the whole document set (or digests of it, when it exceeds `full_context_words`), so the modules reflect the actual system rather than whatever a few searches returned.
- **Hybrid retrieval.** BM25 catches exact terms (IDs, numbers, jargon) and dense embeddings catch paraphrases; Reciprocal Rank Fusion merges the two rankings.
- **Agent-generated queries.** Each module's search queries are written by the Scope Analyst in the vocabulary of the documents.
- **Coverage check.** Any passage that no requirement cites is re-read, so requirements that retrieval missed are recovered.
- **Grounding and traceability.** Every excerpt carries an ID (such as `D2-001`). Requirements must cite IDs, citations to unknown IDs are removed and flagged, and the FRD ends with a traceability matrix.
- **Structured outputs.** Each agent returns JSON that is validated with Pydantic. Invalid replies are sent back to the model to repair.
- **Critique and refine.** The Reviewer flags problems and only the flagged requirements are rewritten. Conflicts between sources become open questions instead of being decided silently.
- **Deterministic rendering.** The LLM produces content, and code builds the document layout, IDs and tables.
- **Resilience on free tiers.** Primary/fallback models with exponential backoff on 429s, plus an on-disk cache of validated replies so an interrupted run resumes without spending requests again.

## Free-tier limits

OpenRouter free models allow 50 requests per day (1,000 per day once the account has $10 of credit). A typical run makes roughly 8 to 15 calls. When every model is rate-limited, the client backs off before giving up; retrying resumes from cached steps.

To use other models, set PRIMARY_MODEL and FALLBACK_MODEL in .env to any OpenRouter model ID.

The web server keeps jobs in memory. Uploaded files and results are stored under `runs/<job-id>/`, but the job list resets when the server restarts.
