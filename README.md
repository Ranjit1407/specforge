# SpecForge

Generates a Functional Requirements Document (FRD) from any set of source documents (BRDs, meeting notes, emails, specs, RFPs) using a chain of LLM agents over a hybrid RAG index. It is domain-agnostic: modules, user roles, search queries and requirements all come from the documents you upload. Runs on free models through OpenRouter.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env   # then put your OpenRouter key in .env

.\.venv\Scripts\python -m specforge.web     # web UI at http://127.0.0.1:8000
```

In the web UI, drop in your documents, optionally name the project, and click **Generate FRD**. You can watch each agent's progress live, then preview the FRD and download it as Word, Markdown or JSON.

Command-line equivalent:

```powershell
.\.venv\Scripts\python -m specforge path\to\docs another.pdf -o output\myproject --title "My Project"
```

Options: `--top-k N` (chunks per query), `--no-dense` (BM25 only, no embedding download), `--no-cache` (ignore cached replies). Inputs: `.pdf`, `.docx`, `.md`, `.txt`.

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
