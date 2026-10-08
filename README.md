# SpecForge

SpecForge generates a Functional Requirements Document (FRD) from any set of source documents (BRDs, meeting notes, emails, specs, RFPs) using a chain of LLM agents over a hybrid RAG index. It is domain-agnostic: modules, user roles, search queries and requirements all come from the documents you provide. It runs on free models through OpenRouter.

Current version: **1.2.0**. See [CHANGELOG.md](CHANGELOG.md) for release history.

## Contents

- [Features](#features)
- [Installation and setup](#installation-and-setup)
- [Configuration](#configuration)
- [Usage](#usage)
- [How it works](#how-it-works)
- [Project structure](#project-structure)
- [Prompt management](#prompt-management)
- [Git workflow](#git-workflow)
- [Development and contribution guidelines](#development-and-contribution-guidelines)
- [Free-tier limits and runtime notes](#free-tier-limits-and-runtime-notes)

## Features

- **Any input shape**: a single file, several files, folders and nested subfolders (`.pdf`, `.docx`, `.md`, `.txt`), from the web UI or the CLI, with validation, deduplication and a per-file status report.
- **Seven-agent pipeline**: reader, scope analyst, per-module requirements extractor, coverage sweep, reviewer, refiner and writer.
- **Grounded and traceable**: every requirement cites the source passages it came from, and the FRD ends with a traceability matrix.
- **Structured output**: requirements with IDs, priorities (Must, Should, Could), actors, inputs and outputs, business rules and acceptance criteria, plus open questions for stakeholders.
- **Exports**: Word (`FRD.docx`), Markdown (`FRD.md`) and JSON (`frd.json`).
- **Web UI**: drag-and-drop or folder upload, live progress, a Stop button, and an FRD preview with downloads.
- **Resilient on free models**: model fallback, exponential backoff and an on-disk cache, so interrupted runs resume.
- **Managed prompts**: every prompt is a versioned file with documented history (see [Prompt management](#prompt-management)).

## Installation and setup

Requirements: Python 3.11 or newer, Git, and an OpenRouter API key (free at openrouter.ai).

Windows (PowerShell):

```powershell
git clone <repository-url> SpecForge
cd SpecForge
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env      # then put your OpenRouter key in .env
.\.venv\Scripts\python -m specforge --version
```

macOS or Linux:

```bash
git clone <repository-url> SpecForge
cd SpecForge
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env        # then put your OpenRouter key in .env
.venv/bin/python -m specforge --version
```

The first run downloads the embedding model (about 67 MB) into `.cache/`. Use `--no-dense` to skip it and use keyword search only.

## Configuration

Settings are read from `.env` in the project root. `.env` is git-ignored; `.env.example` is the committed template. Never put keys in code, prompts or committed files.

| Variable | Required | Meaning |
| --- | --- | --- |
| `OPENROUTER_API_KEY` | Yes | Your OpenRouter API key |
| `PRIMARY_MODEL` | No | OpenRouter model ID tried first (default `google/gemma-4-31b-it:free`) |
| `FALLBACK_MODEL` | No | Comma-separated model IDs tried in order when the primary fails or is rate-limited |

Example:

```ini
OPENROUTER_API_KEY=sk-or-v1-...
PRIMARY_MODEL=qwen/qwen3.8-27b:free
FALLBACK_MODEL=google/gemma-4-31b-it:free,nvidia/nemotron-3-super-120b-a12b:free
```

Pipeline defaults (passage size, retrieval depth, context budget, retries, cache) are in [specforge/config.py](specforge/config.py), and the most useful ones can be overridden per run with CLI options.

## Usage

### Web UI

```powershell
.\.venv\Scripts\python -m specforge.web               # http://127.0.0.1:8000
.\.venv\Scripts\python -m specforge.web --port 8080   # another port
```

The server listens on this computer only. `--host 0.0.0.0` makes it reachable from your network, but it has no login, so use that only on a trusted network.

In the web UI, drop in files or whole folders (or use **Select a folder**), optionally name the project, and click **Generate FRD**. You can watch each agent's progress live, then preview the FRD and download it as Word, Markdown or JSON.

### Command line

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

Run `.\.venv\Scripts\python -m specforge --help` for the full option list.

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

## Project structure

```
SpecForge/
├── specforge/
│   ├── __init__.py          package version
│   ├── __main__.py          CLI: python -m specforge
│   ├── web.py               FastAPI server, job queue and downloads: python -m specforge.web
│   ├── static/index.html    web UI (one file, vanilla JavaScript, no build step)
│   ├── pipeline.py          runs the eight stages and writes the outputs
│   ├── ingest.py            input collection, validation, text extraction and chunking
│   ├── retriever.py         hybrid BM25 + embedding search fused with RRF
│   ├── agents.py            the seven agents
│   ├── prompts.py           prompt catalog loader and CLI: python -m specforge.prompts
│   ├── llm.py               OpenRouter client: fallback, backoff, JSON repair, cache, cancellation
│   ├── models.py            Pydantic schemas for the FRD and its parts
│   ├── render.py            Word and Markdown rendering
│   └── config.py            settings and .env loading
├── prompts/
│   ├── runtime/             prompts sent to the model, one folder per prompt, one file per version
│   └── development/         requests that built the project, versioned the same way
├── requirements.txt
├── .env.example             configuration template (copy to .env)
├── .gitignore, .gitattributes
├── CHANGELOG.md
└── README.md
```

Created at run time and git-ignored: `.env`, `.venv/`, `.cache/` (embedding model and cached model replies), `runs/` (web jobs: uploads and results), `output/` (CLI results).

## Prompt management

Every prompt SpecForge sends to a model is a versioned file under `prompts/`, not a string inside the code. The requests used to build the project are stored the same way, so the whole prompt history can be searched, reviewed, reused and tracked in Git.

```
prompts/
├── runtime/                    loaded by the application at run time
│   └── <prompt_id>/
│       ├── 1.0.0.toml          older versions stay as history
│       └── 1.1.0.toml          the highest version is the one in use
└── development/                requests used to build SpecForge (documentation only)
    └── <prompt_id>/<version>.toml
```

Each file holds one version of one prompt:

| Field | Meaning |
| --- | --- |
| `id`, `version` | Identifier (matches the folder) and semantic version (matches the file name) |
| `title`, `purpose` | What the prompt is for |
| `kind` | `template` (filled with `str.format`), `fragment` (shared text inserted into templates) or `development` |
| `date`, `changes` | When this version was made, and what changed and why |
| `used_by`, `variables`, `output` | Runtime prompts: the calling code, the `{placeholders}` it fills, and the expected reply |
| `status`, `outcome` | Development prompts: implemented, reverted and so on, and what the request produced |
| `template` | The prompt text, as a TOML literal string (`'''...'''`), so braces and backslashes need no escaping |

The code asks for prompts by id, for example `prompts.render("scope_analyst", ...)` or `prompts.text("grounding")`, and [specforge/prompts.py](specforge/prompts.py) loads the highest version. Shared instructions such as the grounding rule are fragments, so each instruction is stored once.

### Prompt commands

```powershell
.\.venv\Scripts\python -m specforge.prompts list                      # current version of every prompt
.\.venv\Scripts\python -m specforge.prompts list --category development
.\.venv\Scripts\python -m specforge.prompts show scope_analyst         # text and metadata (--version X for an older one)
.\.venv\Scripts\python -m specforge.prompts history scope_analyst      # every version with its change notes
.\.venv\Scripts\python -m specforge.prompts search acceptance criteria # all versions containing these words
.\.venv\Scripts\python -m specforge.prompts bump scope_analyst         # copy the current version to the next one
.\.venv\Scripts\python -m specforge.prompts check                      # validate files, versions, secrets, README sync
.\.venv\Scripts\python -m specforge.prompts sync-readme                # regenerate the catalog below
```

### Changing a prompt

1. Create the next version: `python -m specforge.prompts bump <id> --part minor`. The current file is never edited; it stays as history.
2. Edit the new file's `template`, and replace the `changes` placeholder with what changed and why.
3. Run `python -m specforge.prompts check`, then `python -m specforge.prompts sync-readme`.
4. Test with a real run. Cached replies are keyed by the exact prompt text, so a changed prompt always calls the model again.
5. Commit the new file and README together, for example `git commit -m "prompt(scope_analyst): v1.1.0 ask for 3-12 modules"`.

Choose the version part like this:

- **MAJOR**: the placeholders or the required reply shape change. The calling code must change in the same commit.
- **MINOR**: instructions or wording change in a way meant to change results.
- **PATCH**: typos or formatting, with no intended change in behaviour.

To roll back, bump again and paste the older text, so history only moves forward. `check` rejects a new version whose text equals the previous one, a `changes` note left as the placeholder, placeholders that do not match `variables`, and anything that looks like an API key or token.

### Prompt catalog

The tables and texts below are generated from `prompts/` by `sync-readme`, and `check` fails when they drift, so they always match what the application uses. The runtime prompts are professional, implementation-ready instructions and were kept word for word when they moved into the catalog (version 1.0.0), so results and cached replies are unchanged.

<!-- BEGIN GENERATED: prompt catalog. Edit prompts/ and run `python -m specforge.prompts sync-readme`. -->

#### Runtime prompts

These are sent to the model by the application. Templates are filled with `str.format`; fragments are shared instructions inserted into templates.

| Prompt | Version | Kind | Purpose | Used by | Output |
| --- | --- | --- | --- | --- | --- |
| `document_reader` | 1.0.0 | template | For document sets larger than full_context_words, condenses each part into dense notes under fixed FRD headings, keeping excerpt IDs so later agents can still cite sources. Not called when the whole set fits the context budget. | specforge/agents.py: document_reader() | Plain-text notes (LLM.complete) |
| `scope_analyst` | 1.0.0 | template | Establishes the project context from the whole material (or the reader's notes): title, purpose, scope in and out, stakeholders, assumptions, constraints, and the functional modules with search queries written in the documents' own vocabulary. | specforge/agents.py: scope_analyst() | JSON validated as models.ProjectContext |
| `requirements_extractor` | 1.0.0 | template | Runs once per module on the passages retrieved for it and extracts every supported functional requirement, one behaviour each, with priority, acceptance criteria and citations. | specforge/agents.py: requirements_extractor() | JSON validated as models.RequirementList |
| `coverage_sweep` | 1.0.0 | template | Re-reads passages that no requirement cites yet and extracts only the requirements the existing set misses, assigning each to a module. | specforge/agents.py: coverage_sweep() | JSON validated as models.RequirementList |
| `reviewer` | 1.0.0 | template | Reviews the numbered requirement set as a whole and reports duplicates to merge, requirements that are ambiguous, conflicting, incomplete or untestable, and open questions for stakeholders. | specforge/agents.py: reviewer() | JSON validated as models.Review |
| `refiner` | 1.0.0 | template | Rewrites only the requirements the reviewer flagged, using their cited and related passages; issues the sources cannot resolve are explained in notes for stakeholders. | specforge/agents.py: refiner() | JSON validated as models.RequirementList |
| `writer` | 1.0.0 | template | Writes the executive summary and a short overview of each module from the project purpose and the final requirements, without adding features. | specforge/agents.py: writer() | JSON validated as models.Summary |
| `json_repair` | 1.0.0 | template | Sent back to the model when a reply is not valid JSON or does not match the expected schema, asking it to fix the structure without dropping content. | specforge/llm.py: LLM.complete_json() | Corrected JSON in the originally requested shape |
| `grounding` | 1.0.0 | fragment | Shared instruction that keeps every agent tied to the source material: no invented features, values, roles or rules, and empty fields where the sources are silent. | Inserted as {grounding} into document_reader, scope_analyst, requirements_extractor, coverage_sweep and refiner | Text fragment |
| `requirement_fields` | 1.0.0 | fragment | Shared definition of the fields every extracted requirement must carry: a 3-8 word title, one testable 'The system shall' sentence, actor, priority rules, inputs and outputs, business rules, 2-4 acceptance criteria and at least one source citation. | Inserted as {fields} into requirements_extractor and coverage_sweep | Text fragment |

<details>
<summary><code>document_reader</code> v1.0.0: Agent 1: Document Reader</summary>

**Purpose:** For document sets larger than full_context_words, condenses each part into dense notes under fixed FRD headings, keeping excerpt IDs so later agents can still cite sources. Not called when the whole set fits the context budget.  
**Used by:** specforge/agents.py: document_reader()  
**Variables:** `{part}`, `{parts}`, `{grounding}`, `{excerpts}`  
**Output:** Plain-text notes (LLM.complete)  
**File:** `prompts/runtime/document_reader/1.0.0.toml`

```text
You are a business analyst reading part {part} of {parts} of a project's source documents.

Write dense notes on everything in this part that matters for a Functional Requirements Document, under these headings:
Purpose and goals; Users and roles; Features and behaviours; Business rules and exact values; Data and records; Integrations and external systems; Constraints and non-functional needs; Explicitly out of scope; Open issues or conflicts.

Put the excerpt IDs in brackets after each point, e.g. [D1-004]. Skip a heading when this part says nothing about it. {grounding}

EXCERPTS:
{excerpts}
```

</details>

<details>
<summary><code>scope_analyst</code> v1.0.0: Agent 2: Scope Analyst</summary>

**Purpose:** Establishes the project context from the whole material (or the reader's notes): title, purpose, scope in and out, stakeholders, assumptions, constraints, and the functional modules with search queries written in the documents' own vocabulary.  
**Used by:** specforge/agents.py: scope_analyst()  
**Variables:** `{grounding}`, `{title_hint}`, `{material}`  
**Output:** JSON validated as models.ProjectContext  
**File:** `prompts/runtime/scope_analyst/1.0.0.toml`

```text
You are a senior business analyst preparing a Functional Requirements Document (FRD).
Read the source material below, whatever domain it is from, and establish the project context.

Identify the functional modules of the system: distinct capability areas named after what the system does in this domain, not after document sections. Use as many as the material needs, usually 3 to 10; a small system may need only 2. Every feature in the material should belong to one module. For each module write 3 or 4 short search queries in the vocabulary of the sources; they will be used to retrieve the evidence for that module's requirements.

{grounding}

Return only a JSON object of this shape:
{{
  "title": "system or project name",
  "purpose": "2-3 sentences on the business problem and the goal",
  "scope_in": ["capability in scope"],
  "scope_out": ["item explicitly out of scope"],
  "stakeholders": [{{"role": "role name", "description": "what they need from the system"}}],
  "assumptions": ["assumption stated or clearly implied by the sources"],
  "constraints": ["constraint: regulatory, technical, budget, timeline"],
  "modules": [{{"name": "module name", "description": "what this module does", "search_queries": ["query"]}}]
}}
{title_hint}
{material}
```

</details>

<details>
<summary><code>requirements_extractor</code> v1.0.0: Agent 3: Requirements Extractor</summary>

**Purpose:** Runs once per module on the passages retrieved for it and extracts every supported functional requirement, one behaviour each, with priority, acceptance criteria and citations.  
**Used by:** specforge/agents.py: requirements_extractor()  
**Variables:** `{module}`, `{title}`, `{description}`, `{others}`, `{fields}`, `{grounding}`, `{excerpts}`  
**Output:** JSON validated as models.RequirementList  
**File:** `prompts/runtime/requirements_extractor/1.0.0.toml`

```text
You are a business analyst writing the functional requirements for the "{module}" module of {title}.

Module scope: {description}
Other modules (leave their requirements to them): {others}

Extract every functional requirement for this module that the source excerpts support. A functional requirement is a behaviour the system must perform. Split compound statements so each requirement covers one behaviour.

{fields}

{grounding} If the excerpts hold nothing for this module, return an empty list.

Return only a JSON object of this shape:
{{"requirements": [{{"title": "", "description": "The system shall ...", "actor": "", "priority": "Must", "inputs": [], "outputs": [], "business_rules": [], "acceptance_criteria": [], "sources": ["D1-001"]}}]}}

SOURCE EXCERPTS:
{excerpts}
```

</details>

<details>
<summary><code>coverage_sweep</code> v1.0.0: Agent 4: Coverage Sweep</summary>

**Purpose:** Re-reads passages that no requirement cites yet and extracts only the requirements the existing set misses, assigning each to a module.  
**Used by:** specforge/agents.py: coverage_sweep()  
**Variables:** `{title}`, `{modules}`, `{fields}`, `{grounding}`, `{existing}`, `{excerpts}`  
**Output:** JSON validated as models.RequirementList  
**File:** `prompts/runtime/coverage_sweep/1.0.0.toml`

```text
You are a business analyst checking that the FRD for {title} misses nothing.

No requirement cites the excerpts below yet. Many may be background with no requirements in them. Extract only the functional requirements they contain that the existing requirements do not already cover, and assign each to the best-fitting module: {modules}.

{fields}
- module: one of the module names above, spelled exactly.

{grounding} Returning an empty list is fine when there is nothing new.

Return only a JSON object of this shape:
{{"requirements": [{{"module": "", "title": "", "description": "The system shall ...", "actor": "", "priority": "Must", "inputs": [], "outputs": [], "business_rules": [], "acceptance_criteria": [], "sources": ["D1-001"]}}]}}

EXISTING REQUIREMENTS:
{existing}

UNCITED EXCERPTS:
{excerpts}
```

</details>

<details>
<summary><code>reviewer</code> v1.0.0: Agent 5: Reviewer</summary>

**Purpose:** Reviews the numbered requirement set as a whole and reports duplicates to merge, requirements that are ambiguous, conflicting, incomplete or untestable, and open questions for stakeholders.  
**Used by:** specforge/agents.py: reviewer()  
**Variables:** `{title}`, `{requirements}`  
**Output:** JSON validated as models.Review  
**File:** `prompts/runtime/reviewer/1.0.0.toml`

```text
You are a requirements quality reviewer for the FRD of {title}.

Review the requirements below as a set and report:
1. merges: requirements that describe the same behaviour (often across modules). Name the one to keep and the ones to drop.
2. issues: requirements that are ambiguous (vague words such as "fast", "easy", "appropriate"), conflicting with another requirement, incomplete (missing a rule, limit or outcome), or untestable. Give a concrete note saying what is wrong.
3. open_questions: decisions the stakeholders must make before build, such as conflicts the sources do not resolve and missing values.

Report only real problems; an empty list is fine.

Return only a JSON object of this shape:
{{"merges": [{{"keep": "FR-X-001", "drop": ["FR-Y-003"], "reason": ""}}],
 "issues": [{{"req_id": "FR-X-002", "type": "ambiguous|conflict|incomplete|untestable", "note": ""}}],
 "open_questions": ["question"]}}

REQUIREMENTS:
{requirements}
```

</details>

<details>
<summary><code>refiner</code> v1.0.0: Agent 6: Refiner</summary>

**Purpose:** Rewrites only the requirements the reviewer flagged, using their cited and related passages; issues the sources cannot resolve are explained in notes for stakeholders.  
**Used by:** specforge/agents.py: refiner()  
**Variables:** `{grounding}`, `{flagged}`, `{excerpts}`  
**Output:** JSON validated as models.RequirementList  
**File:** `prompts/runtime/refiner/1.0.0.toml`

```text
You are a business analyst fixing requirements that a reviewer flagged.

For each requirement below, rewrite it to resolve the reviewer's note using the source excerpts: make it specific and testable, add the missing rule or value, and tighten its acceptance criteria. Keep its id.
If the sources cannot resolve the issue (for example two sources conflict), keep the best supported wording and put a one-sentence explanation in "notes" so a stakeholder can decide.

{grounding}

Return only a JSON object of this shape, with one entry per flagged requirement:
{{"requirements": [{{"id": "FR-X-001", "title": "", "description": "The system shall ...", "actor": "", "priority": "Must", "inputs": [], "outputs": [], "business_rules": [], "acceptance_criteria": [], "sources": ["D1-001"], "notes": ["..."]}}]}}

FLAGGED REQUIREMENTS (with reviewer notes):
{flagged}

SOURCE EXCERPTS:
{excerpts}
```

</details>

<details>
<summary><code>writer</code> v1.0.0: Agent 7: Writer</summary>

**Purpose:** Writes the executive summary and a short overview of each module from the project purpose and the final requirements, without adding features.  
**Used by:** specforge/agents.py: writer()  
**Variables:** `{title}`, `{purpose}`, `{requirements}`  
**Output:** JSON validated as models.Summary  
**File:** `prompts/runtime/writer/1.0.0.toml`

```text
You are a technical writer finishing the Functional Requirements Document for {title}.

Purpose: {purpose}

Write:
- executive_summary: one or two paragraphs for business readers covering what the system does, who it serves, and the main capabilities.
- module_overviews: for each module, 2-3 sentences introducing it, keyed by the exact module name.

Base everything on the purpose and the requirements below; add no new features.

Return only a JSON object of this shape:
{{"executive_summary": "", "module_overviews": {{"Module Name": ""}}}}

REQUIREMENTS BY MODULE:
{requirements}
```

</details>

<details>
<summary><code>json_repair</code> v1.0.0: JSON repair</summary>

**Purpose:** Sent back to the model when a reply is not valid JSON or does not match the expected schema, asking it to fix the structure without dropping content.  
**Used by:** specforge/llm.py: LLM.complete_json()  
**Variables:** `{error}`, `{raw}`  
**Output:** Corrected JSON in the originally requested shape  
**File:** `prompts/runtime/json_repair/1.0.0.toml`

```text
Your previous reply could not be parsed or did not match the required JSON shape.

Error:
{error}

Previous reply:
{raw}

Return the corrected JSON only, with no commentary and no code fences. Keep all the content; fix only the structure.
```

</details>

<details>
<summary><code>grounding</code> v1.0.0: Grounding rule</summary>

**Purpose:** Shared instruction that keeps every agent tied to the source material: no invented features, values, roles or rules, and empty fields where the sources are silent.  
**Used by:** Inserted as {grounding} into document_reader, scope_analyst, requirements_extractor, coverage_sweep and refiner  
**Output:** Text fragment  
**File:** `prompts/runtime/grounding/1.0.0.toml`

```text
Use only the source material. Do not invent features, values, roles or rules it does not support. When the sources are silent on something, leave that field empty.
```

</details>

<details>
<summary><code>requirement_fields</code> v1.0.0: Requirement field guide</summary>

**Purpose:** Shared definition of the fields every extracted requirement must carry: a 3-8 word title, one testable 'The system shall' sentence, actor, priority rules, inputs and outputs, business rules, 2-4 acceptance criteria and at least one source citation.  
**Used by:** Inserted as {fields} into requirements_extractor and coverage_sweep  
**Output:** Text fragment  
**File:** `prompts/runtime/requirement_fields/1.0.0.toml`

```text
For each requirement:
- title: 3-8 words.
- description: one "The system shall ..." sentence, specific and testable. Keep exact values from the sources (limits, time windows, amounts, formats).
- actor: the user role or external system that triggers or uses it.
- priority: "Must", "Should" or "Could". Follow the sources; when they say nothing, use "Must" for core flows and "Should" otherwise.
- inputs / outputs: data the behaviour consumes and produces, if the sources say.
- business_rules: rules and validations that constrain it, if the sources say.
- acceptance_criteria: 2-4 checkable criteria, in Given/When/Then form where it fits.
- sources: IDs of the excerpts that support it, e.g. ["D1-004"]. At least one.
```

</details>

#### Development prompts

Requests that shaped the codebase, in order. Version 1.0.0 is the request as originally written (credentials redacted); 1.1.0, where present, is a professional rewrite with the same intent, ready to reuse.

| Prompt | Version | Status | Date | Request |
| --- | --- | --- | --- | --- |
| `dev-01-rag-agent-chain-design` | 1.1.0 | answered | 2026-10-01 | Design a RAG agent chain that generates an FRD |
| `dev-02-build-on-free-models` | 1.1.0 | implemented | 2026-10-01 | Build the project on free OpenRouter models |
| `dev-03-upload-frontend` | 1.1.0 | implemented | 2026-10-01 | Add a web frontend for uploading documents |
| `dev-04-domain-agnostic` | 1.1.0 | implemented | 2026-10-01 | Make generation domain-agnostic |
| `dev-05-web-hosting` | 1.1.0 | implemented | 2026-10-01 | Host the application as a local web server |
| `dev-06-bug-fixes-corporate-ui` | 1.1.0 | implemented | 2026-10-01 | Fix bugs and restyle the frontend in a corporate style |
| `dev-07-repository-cleanup` | 1.1.0 | implemented | 2026-10-01 | Remove unused code and files |
| `dev-08-model-and-key-update` | 1.1.0 | implemented | 2026-10-01 | Switch the API key and use Qwen as the primary model |
| `dev-09-stop-button` | 1.1.0 | implemented | 2026-10-01 | Add a Stop button for long-running jobs |
| `dev-10-remove-samples` | 1.1.0 | implemented | 2026-10-07 | Remove the bundled sample documents |
| `dev-11-huggingface-kimi-provider` | 1.1.0 | reverted | 2026-10-07 | Use Kimi-K3 through the Hugging Face router |
| `dev-12-remove-kimi` | 1.1.0 | implemented | 2026-10-07 | Remove the Kimi-K3 and Hugging Face provider |
| `dev-13-input-sources` | 1.0.0 | implemented | 2026-10-08 | Support file, folder and CLI inputs with per-file tracking |
| `dev-14-git-and-prompt-management` | 1.0.0 | implemented | 2026-10-08 | Add Git version control and prompt management |

<details>
<summary><code>dev-01-rag-agent-chain-design</code> v1.1.0: Design a RAG agent chain that generates an FRD</summary>

**Purpose:** Ask for the architecture, tools and techniques for generating an FRD from input documents with a chain of agents and RAG.  
**Outcome:** Recommended a hybrid-retrieval, multi-agent design with grounding, citations, schema-validated outputs and a review step; this became SpecForge's architecture.  
**File:** `prompts/development/dev-01-rag-agent-chain-design/1.1.0.toml`

```text
Design a system that generates a Functional Requirements Document (FRD) from a set of input documents using a chain of LLM agents with Retrieval-Augmented Generation (RAG).

Provide:
1. The architecture: the agents in the chain, the responsibility of each, and the data passed between them.
2. The retrieval approach: how documents are parsed, chunked, indexed and searched so that each agent receives the relevant evidence.
3. The techniques that keep the output accurate: grounding in the sources, citations to source passages, schema-validated structured outputs, and a review step that finds duplicates, conflicts and gaps.
4. The recommended tools and libraries for each part, with the reason for each choice.

The FRD must contain the project context, functional requirements grouped by module with priorities and acceptance criteria, open questions, and traceability from every requirement back to its source documents.
```

Previous version 1.0.0 (2026-10-01): Original request as written.

```text
Build a chain of agents using RAG (Retrieval-Augmented Generation) to generate an FRD based on a set of input documents. give me the tools and technique for this
```

</details>

<details>
<summary><code>dev-02-build-on-free-models</code> v1.1.0: Build the project on free OpenRouter models</summary>

**Purpose:** Implement the designed system as a runnable project that uses free Gemma 4 models through OpenRouter.  
**Outcome:** The SpecForge package: ingestion, hybrid retriever, seven-agent pipeline, OpenRouter client with fallback, backoff and caching, CLI, and Word/Markdown/JSON export.  
**File:** `prompts/development/dev-02-build-on-free-models/1.1.0.toml`

```text
Implement the FRD generation system designed in dev-01 as a runnable Python project.

Model access:
- Use free models through OpenRouter: Gemma 4 31B (google/gemma-4-31b-it:free) as the primary model and Gemma 4 26B A4B (google/gemma-4-26b-a4b-it:free) as the fallback.
- Read the OpenRouter API key from a .env file that is excluded from version control, and provide a .env.example. Never hardcode the key.
- Free models are rate-limited: retry with exponential backoff, fall back between models, and cache validated replies on disk so that an interrupted run can resume.

Deliver ingestion of PDF, Word, Markdown and text files, the hybrid retrieval index, the agent chain, Word, Markdown and JSON export of the FRD, a command-line entry point, and a README with setup and usage instructions.
```

Previous version 1.0.0 (2026-10-01): Original request as written; the API key it contained is redacted.

```text
use free llm sk-or-v1-[REDACTED] Gemma 4 26B A4BGemma 4 31B now build the project
```

</details>

<details>
<summary><code>dev-03-upload-frontend</code> v1.1.0: Add a web frontend for uploading documents</summary>

**Purpose:** Give users a browser interface to upload source documents and get the FRD.  
**Outcome:** Upload page served by FastAPI, with live progress and FRD preview and download.  
**File:** `prompts/development/dev-03-upload-frontend/1.1.0.toml`

```text
Add a browser-based frontend so that users can generate an FRD without the command line.

- Let the user select or drag and drop one or more source documents (PDF, Word, Markdown, text), and validate type and size before upload.
- Optionally accept a project name.
- Start generation on the server and show progress while the agents run.
- When the run finishes, show the FRD and offer it for download as Word, Markdown and JSON.
- Show clear, non-technical messages when an upload or a run fails.

Reuse the existing pipeline on the server; the frontend must not duplicate any generation logic.
```

Previous version 1.0.0 (2026-10-01): Original request as written.

```text
create a frontend to ask the user to upload the document
```

</details>

<details>
<summary><code>dev-04-domain-agnostic</code> v1.1.0: Make generation domain-agnostic</summary>

**Purpose:** Make the system understand documents from any domain instead of one specific case.  
**Outcome:** Modules, roles and search queries are derived from the documents by the Scope Analyst; domain-specific assumptions were removed and the Document Reader and Coverage Sweep agents were added.  
**File:** `prompts/development/dev-04-domain-agnostic/1.1.0.toml`

```text
Make FRD generation work for documents from any domain, not only the sample case.

- Remove every domain-specific assumption from the code and prompts: module names, roles, terminology and example values.
- Derive the project's modules, user roles and constraints from the uploaded documents, and generate each module's retrieval queries in the documents' own vocabulary.
- Build an understanding of the whole document set before extracting requirements, so the FRD's structure reflects the system the documents describe.
- Make sure requirements that retrieval misses are still found, for example by re-checking passages that no requirement cites.

Verify the result with documents from at least two unrelated domains.
```

Previous version 1.0.0 (2026-10-01): Original request as written.

```text
i want it for generic not for specific case,the code should understand the document and provide the frd
```

</details>

<details>
<summary><code>dev-05-web-hosting</code> v1.1.0: Host the application as a local web server</summary>

**Purpose:** Run SpecForge as a web application that is used from a browser.  
**Outcome:** `python -m specforge.web` serves the UI and API at http://127.0.0.1:8000 with a background job queue and progress streaming.  
**File:** `prompts/development/dev-05-web-hosting/1.1.0.toml`

```text
Run the application as a web server so that it can be used from a browser.

- Serve the frontend and the API from one process with a single start command.
- Run generation jobs in the background so requests return immediately. Process jobs one at a time, because the free model tier is rate-limited per account.
- Stream progress to the browser while a job runs, and keep results downloadable after it finishes.
- Store each job's uploads and outputs in its own folder, independent of the directory the server was started from.
- Document the start command and the URL in the README.
```

Previous version 1.0.0 (2026-10-01): Original request as written.

```text
host it on web
```

</details>

<details>
<summary><code>dev-06-bug-fixes-corporate-ui</code> v1.1.0: Fix bugs and restyle the frontend in a corporate style</summary>

**Purpose:** Find and fix defects across the application and give the frontend a professional corporate design.  
**Outcome:** Fixes to logging, error messages, cache paths, module de-duplication and exports; a navy and slate UI with a three-step flow, stage descriptions, summary figures and dark mode.  
**File:** `prompts/development/dev-06-bug-fixes-corporate-ui/1.1.0.toml`

```text
Review the whole application for defects and fix them, then redesign the frontend in a professional corporate style.

Bug review: check the pipeline, the server and the frontend for errors, inconsistent state, unclear error messages, paths that depend on the working directory, and anything that exposes implementation details (model names, raw API errors) to users. Fix each problem at its root and verify the fix.

Frontend redesign:
- A restrained corporate look: navy and slate palette, clear typography and consistent spacing.
- A visible three-step flow: upload, analysis, review and export.
- Progress that explains each stage in business terms, with the elapsed time.
- A result view with summary figures, downloads and a navigable FRD preview.
- A responsive layout, dark mode and accessible markup.

Keep all existing functionality working.
```

Previous version 1.0.0 (2026-10-01): Original request as written.

```text
check for any bugs and rectify make the frontend more attractive in corporate style
```

</details>

<details>
<summary><code>dev-07-repository-cleanup</code> v1.1.0: Remove unused code and files</summary>

**Purpose:** Remove dead code and unneeded files from the project without changing behaviour.  
**Outcome:** Unused code, bytecode caches, stale caches and test runs removed; completed FRDs moved to output/ before their job folders were deleted.  
**File:** `prompts/development/dev-07-repository-cleanup/1.1.0.toml`

```text
Clean up the repository without changing behaviour.

- Find unused code (functions, imports, fields) with static analysis and remove it.
- Delete generated and temporary files: Python bytecode caches, stale caches, test outputs and abandoned job folders.
- Keep user data and anything expensive to recreate, such as completed outputs, downloaded models and cached model replies, or move it to the proper output folder first.
- Re-run the tests afterwards to confirm nothing broke, and report what was removed and what was kept.
```

Previous version 1.0.0 (2026-10-01): Original request as written. It was repeated on 2026-10-07 as "remove unwanted files and unused files"; stored once to avoid a duplicate.

```text
remove unwanted and not used files
```

</details>

<details>
<summary><code>dev-08-model-and-key-update</code> v1.1.0: Switch the API key and use Qwen as the primary model</summary>

**Purpose:** Update the OpenRouter key and model configuration so runs are less affected by rate limits.  
**Outcome:** Key stored in .env; Qwen primary with Gemma 4 31B and NVIDIA Nemotron fallbacks; FALLBACK_MODEL accepts a comma-separated list.  
**File:** `prompts/development/dev-08-model-and-key-update/1.1.0.toml`

```text
Update the model configuration.

- Replace the OpenRouter API key with the new one. Store it only in .env, never in code or committed files.
- Use qwen/qwen3.8-27b:free as the primary model.
- Check which free models currently respond, and configure a fallback chain from different providers so that one provider's rate limit does not stop a run.
- Run a live test on sample documents and report the result.
```

Previous version 1.0.0 (2026-10-01): Original request as written; the API key it contained is redacted.

```text
sk-or-v1-[REDACTED] use this key qwen/qwen3.8-27b:free
```

</details>

<details>
<summary><code>dev-09-stop-button</code> v1.1.0: Add a Stop button for long-running jobs</summary>

**Purpose:** Let users stop a generation run that is taking too long.  
**Outcome:** A Stop button appears after 30 seconds, with confirmation; cancelling interrupts in-flight requests and retry waits, and completed steps stay cached for the next run.  
**File:** `prompts/development/dev-09-stop-button/1.1.0.toml`

```text
Let users stop a generation run that is taking too long.

- Show a Stop button on the progress screen once a run has been going for 30 seconds.
- Ask for confirmation before stopping.
- Stop promptly, including while waiting for a model reply or a retry back-off, and free the server for the next job.
- Show the run as stopped rather than failed, and offer to run it again. Steps that already completed must be reused rather than recomputed.
- Cancelling a queued job must stop it before it starts.
```

Previous version 1.0.0 (2026-10-01): Original request as written.

```text
create a stop button to stop the process if it takes more than 30 seconds
```

</details>

<details>
<summary><code>dev-10-remove-samples</code> v1.1.0: Remove the bundled sample documents</summary>

**Purpose:** Remove the example input documents from the project.  
**Outcome:** The samples/ folder was deleted and the README line that referred to it was removed.  
**File:** `prompts/development/dev-10-remove-samples/1.1.0.toml`

```text
Remove the bundled sample documents from the repository, and update any documentation or commands that refer to them.
```

Previous version 1.0.0 (2026-10-07): Original request as written.

```text
remove samples
```

</details>

<details>
<summary><code>dev-11-huggingface-kimi-provider</code> v1.1.0: Use Kimi-K3 through the Hugging Face router</summary>

**Purpose:** Call models through the Hugging Face Inference Providers router, using Kimi-K3 on Together.  
**Outcome:** A configurable provider setting (LLM_PROVIDER) with Kimi-K3 on Together was added, then reverted in dev-12 because the model is paid.  
**File:** `prompts/development/dev-11-huggingface-kimi-provider/1.1.0.toml`

```text
Add support for calling models through the Hugging Face Inference Providers router, following the example below, and use moonshotai/Kimi-K3 served by Together as the primary model.

- Make the provider configurable (OpenRouter or Hugging Face) without code changes, and read the Hugging Face token from HF_TOKEN in .env.
- Keep OpenRouter working so that it is possible to switch back.
- Confirm that the model is available and report its cost before relying on it.
- SpecForge sends text only, so the image input in the example is not needed.

Reference example:

import os
from openai import OpenAI

client = OpenAI(
    base_url="https://router.huggingface.co/v1",
    api_key=os.environ["HF_TOKEN"],
)

completion = client.chat.completions.create(
    model="moonshotai/Kimi-K3:together",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Describe this image in one sentence."
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "https://cdn.britannica.com/61/93061-050-99147DCE/Statue-of-Liberty-Island-New-York-Bay.jpg"
                    }
                }
            ]
        }
    ],
)

print(completion.choices[0].message)
```

Previous version 1.0.0 (2026-10-07): Original request as written.

```text
use this

import os
from openai import OpenAI

client = OpenAI(
    base_url="https://router.huggingface.co/v1",
    api_key=os.environ["HF_TOKEN"],
)

completion = client.chat.completions.create(
    model="moonshotai/Kimi-K3:together",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Describe this image in one sentence."
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "https://cdn.britannica.com/61/93061-050-99147DCE/Statue-of-Liberty-Island-New-York-Bay.jpg"
                    }
                }
            ]
        }
    ],
)

print(completion.choices[0].message)
```

</details>

<details>
<summary><code>dev-12-remove-kimi</code> v1.1.0: Remove the Kimi-K3 and Hugging Face provider</summary>

**Purpose:** Return to OpenRouter's free models as the only backend.  
**Outcome:** The provider switch, Hugging Face settings and related messages and documentation were removed; the free OpenRouter model chain was restored.  
**File:** `prompts/development/dev-12-remove-kimi/1.1.0.toml`

```text
Remove the Hugging Face router and Kimi-K3 support added in dev-11, and return to OpenRouter's free models as the only provider. Remove the related configuration, error messages and documentation, and leave the application configured and working with the free model chain.
```

Previous version 1.0.0 (2026-10-07): Original request as written.

```text
remove kimi k3
```

</details>

<details>
<summary><code>dev-13-input-sources</code> v1.0.0: Support file, folder and CLI inputs with per-file tracking</summary>

**Purpose:** Accept single files, multiple files and nested folders through the CLI and web UI, with validation, deduplication and per-file status.  
**Outcome:** Release 1.1.0: a shared input collection, validation before processing, content or path deduplication, per-file status reporting, folder upload and the --check option.  
**File:** `prompts/development/dev-13-input-sources/1.0.0.toml`

```text
### Input Source Requirement

The system must support individual files, multiple files, folders, and command-line interface (CLI) inputs.

- File Input: Users should be able to provide one or more individual files for processing.
- Folder Input: Users should be able to provide a folder containing multiple files. The system must recursively scan the folder and identify all supported files within nested subfolders.
- CLI Input: The system must provide CLI commands for specifying files, folders, and processing options without requiring the web interface.
- Supported Formats: The system should process all configured document formats and ignore unsupported file types with an appropriate warning.
- Batch Processing: When multiple files or a folder is provided, all supported files should be processed as a single input collection while maintaining individual file identity and metadata.
- File-Level Tracking: The system must retain the original file name, relative folder path, file type, and processing status for every file.
- Duplicate Handling: Duplicate files should be detected and handled according to the configured deduplication policy.
- Error Handling: If an individual file fails during processing, the system should continue processing the remaining valid files and report failed files separately.
- Validation: The system should validate the input path, file accessibility, supported formats, and file integrity before processing begins.
- Consistent Processing: File, folder, and CLI inputs should use the same underlying processing pipeline to ensure consistent results regardless of the input method.

### Expected Input Methods

The system should support:

1. Single file
2. Multiple files
3. Folder containing files
4. Folder containing nested subfolders and files
5. File or folder provided through the CLI
6. CLI options for configuring processing parameters

The system should normalize all input types into a common collection of documents before initiating downstream processing.
```

</details>

<details>
<summary><code>dev-14-git-and-prompt-management</code> v1.0.0: Add Git version control and prompt management</summary>

**Purpose:** Put the project under Git with a meaningful history, and store, version and document all prompts.  
**Outcome:** Release 1.2.0: tagged Git history, a hardened .gitignore, the versioned prompt catalog with its CLI, this development prompt history, the README overhaul and a CHANGELOG.  
**File:** `prompts/development/dev-14-git-and-prompt-management/1.0.0.toml`

```text
Update the existing project by adding Git-based version control and a prompt management system without disrupting the existing functionality.

1. Git Version Control

Integrate Git into the existing project to provide proper source-code version control.

Requirements:
- Initialize the existing project as a Git repository if Git is not already configured.
- Create an appropriate .gitignore file for the project's technology stack.
- Ensure sensitive information such as API keys, passwords, tokens, credentials, .env files, and other secrets are excluded from version control.
- Organize commits using clear, descriptive commit messages.
- Maintain meaningful version history for project changes.
- Use semantic versioning (MAJOR.MINOR.PATCH) where applicable.
- Document the Git workflow and commonly used Git commands in the README.md.
- Do not modify or remove existing project functionality solely for the purpose of introducing Git.

2. Prompt Management

Add a prompt management mechanism to the existing project for storing and maintaining prompts used by the system.

Requirements:
- Store previously used prompts in a dedicated and clearly identifiable location.
- Prompts should be organized so they can be easily searched, reviewed, reused, and updated.
- Each stored prompt should have a meaningful name or identifier.
- Maintain the prompt's purpose, version, and relevant usage information where applicable.
- Avoid storing duplicate prompts unnecessarily.
- Preserve previous prompt versions when a prompt is modified, where practical.
- Ensure prompt changes can be tracked through Git.

3. README Prompt Documentation

Update the existing README.md to document the prompts used by the project.

Requirements:
- Add a dedicated Prompt Management section to README.md.
- Store the current and previously used prompts in a structured format that is easy for developers to understand and maintain.
- If an existing prompt is informal, incomplete, or overly simple, rewrite it into a professional, clear, and implementation-oriented prompt while preserving its original intent.
- Do not change the functional intent of an existing prompt unless required for clarity or correctness.
- Clearly identify the purpose of each prompt.
- Include prompt versions where applicable.
- Document significant changes between prompt versions.
- Keep the README prompt documentation synchronized with the actual prompts used by the application.

4. Prompt Version Control

Prompt modifications must be treated as version-controlled project changes.

For each significant prompt change:
- Assign an appropriate prompt version.
- Record what was changed and why.
- Preserve the previous version when historical tracking is required.
- Commit prompt changes through Git using descriptive commit messages.

5. README Maintenance

The existing README.md should be enhanced rather than unnecessarily replaced.

The README should document:
- Project overview
- Installation and setup
- Existing functionality
- Project structure
- Git setup and workflow
- Prompt management
- Current prompts and their purposes
- Prompt version history
- CLI usage, if supported by the existing project
- Configuration requirements
- Development and contribution guidelines

6. Implementation Constraints

- Treat the current project as the source of truth.
- Inspect the existing project structure and implementation before making changes.
- Preserve all existing functionality unless a change is explicitly required.
- Do not introduce unnecessary dependencies.
- Follow the project's existing coding conventions and technology choices.
- Do not hardcode credentials or sensitive configuration.
- Ensure the project remains runnable after the changes.
- Update documentation to accurately reflect the implemented functionality.
- Keep Git configuration, prompt management, and documentation clean, maintainable, and suitable for future development.

Expected outcome:

The existing project should have a proper Git version-control structure, a maintainable prompt management system, professionally documented prompts, and an updated README.md that provides clear documentation of both the project's development workflow and its prompt history.
```

</details>

#### Prompt version history

| Prompt | Version | Date | Changes |
| --- | --- | --- | --- |
| `document_reader` | 1.0.0 | 2026-10-01 | Initial version, moved unchanged from the source code into the prompt catalog. |
| `scope_analyst` | 1.0.0 | 2026-10-01 | Initial version, moved unchanged from the source code into the prompt catalog. |
| `requirements_extractor` | 1.0.0 | 2026-10-01 | Initial version, moved unchanged from the source code into the prompt catalog. |
| `coverage_sweep` | 1.0.0 | 2026-10-01 | Initial version, moved unchanged from the source code into the prompt catalog. |
| `reviewer` | 1.0.0 | 2026-10-01 | Initial version, moved unchanged from the source code into the prompt catalog. |
| `refiner` | 1.0.0 | 2026-10-01 | Initial version, moved unchanged from the source code into the prompt catalog. |
| `writer` | 1.0.0 | 2026-10-01 | Initial version, moved unchanged from the source code into the prompt catalog. |
| `json_repair` | 1.0.0 | 2026-10-01 | Initial version, moved unchanged from the source code into the prompt catalog. |
| `grounding` | 1.0.0 | 2026-10-01 | Initial version, moved unchanged from the source code into the prompt catalog. |
| `requirement_fields` | 1.0.0 | 2026-10-01 | Initial version, moved unchanged from the source code into the prompt catalog. |
| `dev-01-rag-agent-chain-design` | 1.1.0 | 2026-10-08 | Rewritten as a professional prompt with the same intent: names the deliverables (architecture, retrieval approach, accuracy techniques, tool choices) and what the FRD must contain. |
| `dev-01-rag-agent-chain-design` | 1.0.0 | 2026-10-01 | Original request as written. |
| `dev-02-build-on-free-models` | 1.1.0 | 2026-10-08 | Rewritten professionally with the same intent: names the models by ID, keeps the key in .env instead of the prompt, and lists the deliverables and the handling of free-tier rate limits. |
| `dev-02-build-on-free-models` | 1.0.0 | 2026-10-01 | Original request as written; the API key it contained is redacted. |
| `dev-03-upload-frontend` | 1.1.0 | 2026-10-08 | Rewritten professionally with the same intent: specifies validation, progress, results, downloads and error handling, and reuse of the existing pipeline. |
| `dev-03-upload-frontend` | 1.0.0 | 2026-10-01 | Original request as written. |
| `dev-04-domain-agnostic` | 1.1.0 | 2026-10-08 | Rewritten professionally with the same intent: states what must be derived from the documents, what must be removed, and how to verify it. |
| `dev-04-domain-agnostic` | 1.0.0 | 2026-10-01 | Original request as written. |
| `dev-05-web-hosting` | 1.1.0 | 2026-10-08 | Rewritten professionally with the same intent: defines the single start command, background jobs, progress streaming, per-job storage and documentation. |
| `dev-05-web-hosting` | 1.0.0 | 2026-10-01 | Original request as written. |
| `dev-06-bug-fixes-corporate-ui` | 1.1.0 | 2026-10-08 | Rewritten professionally with the same intent: lists what the bug review covers and the concrete goals of the redesign. |
| `dev-06-bug-fixes-corporate-ui` | 1.0.0 | 2026-10-01 | Original request as written. |
| `dev-07-repository-cleanup` | 1.1.0 | 2026-10-08 | Rewritten professionally with the same intent: separates unused code from generated files, says what must be kept, and requires verification. |
| `dev-07-repository-cleanup` | 1.0.0 | 2026-10-01 | Original request as written. It was repeated on 2026-10-07 as "remove unwanted files and unused files"; stored once to avoid a duplicate. |
| `dev-08-model-and-key-update` | 1.1.0 | 2026-10-08 | Rewritten professionally with the same intent: keeps the key out of the prompt and code, and asks for a cross-provider fallback chain and a live test. |
| `dev-08-model-and-key-update` | 1.0.0 | 2026-10-01 | Original request as written; the API key it contained is redacted. |
| `dev-09-stop-button` | 1.1.0 | 2026-10-08 | Rewritten professionally with the same intent: defines when the button appears, confirmation, how quickly it must stop, and what happens to completed work and queued jobs. |
| `dev-09-stop-button` | 1.0.0 | 2026-10-01 | Original request as written. |
| `dev-10-remove-samples` | 1.1.0 | 2026-10-08 | Rewritten professionally with the same intent: includes updating documentation that refers to the samples. |
| `dev-10-remove-samples` | 1.0.0 | 2026-10-07 | Original request as written. |
| `dev-11-huggingface-kimi-provider` | 1.1.0 | 2026-10-08 | Rewritten professionally with the same intent: states the provider, model, configuration and cost check, and notes that only text input is needed. |
| `dev-11-huggingface-kimi-provider` | 1.0.0 | 2026-10-07 | Original request as written. |
| `dev-12-remove-kimi` | 1.1.0 | 2026-10-08 | Rewritten professionally with the same intent: names what to remove and the configuration to leave in place. |
| `dev-12-remove-kimi` | 1.0.0 | 2026-10-07 | Original request as written. |
| `dev-13-input-sources` | 1.0.0 | 2026-10-08 | Recorded as written: it was already a complete, implementation-ready specification. |
| `dev-14-git-and-prompt-management` | 1.0.0 | 2026-10-08 | Recorded as written: it was already a complete, implementation-ready specification. |

<!-- END GENERATED: prompt catalog -->

## Git workflow

### Repository setup

The project is a Git repository on the `main` branch, with tagged releases (`v1.0.0`, `v1.1.0`, `v1.2.0`). To publish it, create an empty repository on your Git host and run:

```bash
git remote add origin https://github.com/<user>/<repo>.git
git push -u origin main --tags
```

### What is never committed

`.gitignore` excludes secrets (`.env` and every `.env.*` except `.env.example`, key and credential files), the virtual environment, Python caches, run-time data (`.cache/`, `runs/`, `output/`, logs) and editor or OS files. `.gitattributes` stores text with LF line endings and treats Word and PDF files as binary.

- Check that a file is ignored: `git check-ignore -v .env`
- Review what you are about to commit: `git status` and `git diff --staged`
- `python -m specforge.prompts check` also fails if a prompt file contains something that looks like an API key.
- If a secret is ever committed, revoke and rotate it at the provider first. Removing it from history does not recall copies that were already pushed or cloned.

### Branches

- `main` is always runnable. Releases are tagged on it.
- Do work on short-lived branches: `feature/<topic>`, `fix/<topic>`, `docs/<topic>`, or `prompt/<prompt-id>-v<version>` for prompt changes.
- Merge back with a pull request, or locally with `git merge --no-ff <branch>`, after the checks in [Development and contribution guidelines](#development-and-contribution-guidelines) pass.

### Commit messages

- Make one logical change per commit, so it can be reviewed and reverted on its own.
- Subject: an imperative summary of up to about 72 characters, for example `Accept files, folders and nested folders with per-file tracking`.
- Body: what changed and why, wrapped at about 72 characters.
- Prompt changes: start the subject with `prompt(<id>): v<version>`, for example `prompt(reviewer): v1.1.0 flag unmeasurable performance terms`, and commit the new prompt file together with the regenerated README.
- Releases: `Release X.Y.Z`.

### Versioning

The application follows semantic versioning (MAJOR.MINOR.PATCH). The version lives in [specforge/__init__.py](specforge/__init__.py) and is shown by `python -m specforge --version`.

- **MAJOR**: incompatible changes to CLI options, web API endpoints, the `frd.json` structure or configuration variables.
- **MINOR**: new, backward-compatible functionality.
- **PATCH**: backward-compatible bug fixes.

Prompts have their own versions, described in [Changing a prompt](#changing-a-prompt).

To make a release:

```bash
# 1. update __version__ in specforge/__init__.py and add a section to CHANGELOG.md
git add specforge/__init__.py CHANGELOG.md
git commit -m "Release 1.3.0"
git tag -a v1.3.0 -m "SpecForge 1.3.0: <one-line summary>"
git push origin main --tags
```

### Common commands

| Task | Command |
| --- | --- |
| See what changed | `git status`, `git diff`, `git diff --staged` |
| Stage and commit | `git add <files>`, then `git commit` |
| History | `git log --oneline --decorate --graph` |
| History of one prompt | `git log --oneline -- prompts/runtime/reviewer/` |
| Start a branch | `git switch -c feature/<topic>` |
| Switch branch | `git switch main` |
| Merge a branch | `git switch main`, then `git merge --no-ff feature/<topic>` |
| Update from the remote | `git pull --rebase` |
| Publish a branch | `git push -u origin feature/<topic>` |
| List and inspect releases | `git tag -n`, `git show v1.1.0` |
| Compare two releases | `git diff v1.1.0 v1.2.0 --stat` |
| Undo a commit safely | `git revert <commit>` (adds a new commit; history is kept) |
| Set work aside | `git stash -u`, later `git stash pop` |

## Development and contribution guidelines

1. Set up the environment as in [Installation and setup](#installation-and-setup) and create a branch for your change.
2. Follow the existing conventions:
   - Python 3.11+ with type hints and the standard library first.
   - Do not add a dependency without a clear need. When you do, add it to `requirements.txt` with a minimum version.
   - Prompt text belongs only in `prompts/`. Code refers to prompts by id.
   - Settings belong in `config.py` or `.env`. Credentials are never hardcoded.
   - Messages shown to users are plain language, without stack traces, model names or raw API errors.
   - The web UI stays a single static file with no build step. Keep dark mode and the narrow-screen layout working.
3. Check your change before committing. There is no automated test suite yet, so use these:
   - `python -m specforge.prompts check` validates prompts and the README catalog.
   - `python -m specforge <folder-with-test-documents> --check` checks input collection and text extraction without calling the model.
   - A real run on a small document set (`python -m specforge <docs> -o output/test`) exercises the agents. It uses free-tier requests, and replies are cached, so rerunning the same input is fast and free.
   - For web UI changes, run `python -m specforge.web` and try the flow in a browser: upload, progress, Stop, result and downloads.
4. Update the documentation in the same change: this README, `CHANGELOG.md` for user-visible changes, and `sync-readme` for prompt changes.
5. Commit following [Commit messages](#commit-messages) and open a pull request that describes the change, how you checked it, and any prompt versions it introduces.

## Free-tier limits and runtime notes

OpenRouter free models allow 50 requests per day (1,000 per day once the account has $10 of credit). A typical run makes roughly 8 to 15 calls. When every model is rate-limited, the client backs off before giving up; retrying resumes from cached steps.

The web server keeps jobs in memory. Uploaded files and results are stored under `runs/<job-id>/`, but the job list resets when the server restarts.
