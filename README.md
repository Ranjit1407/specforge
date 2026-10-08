# SpecForge

SpecForge generates a Functional Requirements Document (FRD) from any set of source documents (BRDs, meeting notes, emails, specs, RFPs) using a chain of LLM agents over a hybrid RAG index. It is domain-agnostic: modules, user roles, search queries and requirements all come from the documents you provide. It runs on free models through OpenRouter.

Current version: **2.1.0**. See [CHANGELOG.md](CHANGELOG.md) for release history.

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
- **Enterprise FRD template**: a 21-section document covering document information, sign-off authority, team contacts, revision history, objective, overview and scope, end-user roles, the functional requirements summary, role-based access requirements, detailed use cases, the access matrix, assumptions and dependencies, status and priority definitions, non-functional, integration and UI requirements, open questions, the traceability matrix, acceptance criteria and sign-off. See [The FRD template](#the-frd-template).
- **Ten-agent pipeline**: reader, scope analyst, specification analyst, per-module requirements extractor, coverage sweep, reviewer, refiner, access analyst, use case writer and writer.
- **Grounded and traceable**: every requirement cites the source passages it came from, and the FRD ends with a traceability matrix.
- **Nothing invented**: priorities (Critical, High, Medium, Low) are set only when the sources support them, and otherwise marked "To be confirmed". Missing information reads "Not specified in source material", and conflicts become open questions instead of being resolved silently.
- **Exports**: Word (`FRD.docx`), Markdown (`FRD.md`) and JSON (`frd.json`).
- **Web UI**: drag-and-drop or folder upload, live progress, a Stop button, and an FRD preview with downloads.
- **Resilient on free models**: model fallback, exponential backoff and an on-disk cache, so interrupted runs resume.
- **Local fallback**: when every OpenRouter model fails, or the daily free quota is used up, requests go to a local [Ollama](https://ollama.com) model, so a run can still finish. Ollama can also be the only backend.
- **Managed prompts**: every prompt is a versioned file with documented history (see [Prompt management](#prompt-management)).

## Installation and setup

Requirements: Python 3.11 or newer, Git, and an OpenRouter API key (free at openrouter.ai). Optional: Ollama for the local fallback (see [Local fallback with Ollama](#local-fallback-with-ollama)).

Windows (PowerShell):

```powershell
git clone https://github.com/Ranjit1407/specforge.git SpecForge
cd SpecForge
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
copy .env.example .env      # then put your OpenRouter key in .env
.\.venv\Scripts\python -m specforge --version
```

macOS or Linux:

```bash
git clone https://github.com/Ranjit1407/specforge.git SpecForge
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
| `OPENROUTER_API_KEY` | Yes, unless `OLLAMA_MODEL` is set | Your OpenRouter API key |
| `PRIMARY_MODEL` | No | OpenRouter model ID tried first (default `google/gemma-4-31b-it:free`) |
| `FALLBACK_MODEL` | No | Comma-separated model IDs tried in order when the primary fails or is rate-limited |
| `OLLAMA_MODEL` | No | Comma-separated local Ollama models used when every OpenRouter model fails (for example `qwen2.5:7b`) |
| `OLLAMA_URL` | No | Ollama server address (default `http://localhost:11434`) |
| `OLLAMA_NUM_CTX` | No | Largest context window SpecForge asks Ollama for, in tokens (default `32768`) |

Example:

```ini
OPENROUTER_API_KEY=sk-or-v1-...
PRIMARY_MODEL=nvidia/nemotron-3-super-120b-a12b:free
FALLBACK_MODEL=google/gemma-4-31b-it:free,nvidia/nemotron-3-ultra-550b-a55b:free
OLLAMA_MODEL=qwen2.5:7b
```

Pipeline defaults (passage size, retrieval depth, context budget, retries, cache) are in [specforge/config.py](specforge/config.py), and the most useful ones can be overridden per run with CLI options.

Free models come and go on OpenRouter. If a model starts failing with "unavailable for free" (HTTP 404), replace it in `.env`; https://openrouter.ai/models?max_price=0 lists the current free models.

### Local fallback with Ollama

SpecForge can fall back to a model running on your own computer through [Ollama](https://ollama.com):

1. Install Ollama and pull a model that is good at following instructions and writing JSON. `qwen2.5:7b` (about 4.7 GB) runs well on a laptop GPU with 6 GB of memory:
   ```powershell
   ollama pull qwen2.5:7b
   ```
2. Add `OLLAMA_MODEL=qwen2.5:7b` to `.env`, and keep Ollama running (it starts with Windows by default).

How the fallback works:

- Every request tries the OpenRouter models first. If all of them fail in a round (rate-limited, unavailable or erroring), the same request goes straight to the local model instead of waiting through the back-off.
- When OpenRouter reports that the daily free-model limit or the credit balance is used up, or rejects the API key, the rest of the run uses the local model directly.
- With no `OPENROUTER_API_KEY` at all, every request goes to Ollama.
- Local replies use Ollama's JSON mode and a context window sized to each prompt, up to `OLLAMA_NUM_CTX`. A prompt too large for that window is not sent, because Ollama would silently cut off its beginning (the instructions). That can happen with very large document sets; raise `OLLAMA_NUM_CTX` if the model and your memory allow it.
- The Stop button also stops a local generation, so a stopped run does not keep the model busy.
- The log and the run summary name the model that answered each request, for example `ollama:qwen2.5:7b answered in 40s`.

A 7B local model is slower and less thorough than the large hosted models. Expect a full run to take longer, and review its output with that in mind.

## Usage

### Web UI

```powershell
.\.venv\Scripts\python -m specforge.web               # http://127.0.0.1:8000
.\.venv\Scripts\python -m specforge.web --port 8080   # another port
```

The server listens on this computer only. `--host 0.0.0.0` makes it reachable from your network, but it has no login, so use that only on a trusted network.

In the web UI, drop in files or whole folders (or use **Select a folder**), optionally enter the project name and the author (**Prepared by**), and click **Generate FRD**. You can watch each agent's progress live, then preview the FRD and download it as Word, Markdown or JSON.

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
| `--prepared-by NAME` | Author shown in Document Information and the revision history (default: SpecForge automated draft) |
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
   ┌──────────────────────────┴────────────────────────────────────────────────┐
   │  1 Document Reader       small sets: whole text passed on as-is;          │
   │                          large sets: summarised part by part, with IDs    │
   │  2 Scope Analyst         title, purpose, scope, user roles, named people  │
   │                          and sign-off authority, modules + search queries │
   │  3 Specification Analyst assumptions, dependencies, statuses,             │
   │                          non-functional, integration and UI requirements  │
   │  4 Req. Extractor        per module: retrieve → requirements with         │
   │                          priority, acceptance criteria + citations        │
   │  5 Coverage Sweep        excerpts no requirement cites → missed reqs      │
   │  6 Reviewer              duplicates, conflicts, ambiguity, open questions │
   │  7 Refiner               flagged reqs + their cited evidence → rewritten  │
   │  8 Access Analyst        role-based access requirements + access matrix   │
   │  9 Use Case Writer       detailed use cases for the final requirements    │
   │ 10 Writer                objective, overview, purpose of the initiative   │
   └────────────────────────────────────────────────────────────────────────────┘
                              │
     code assigns IDs, merges open questions, builds the 21-section template
                              │
                FRD.docx · FRD.md · frd.json · steps/*.json
```

### The FRD template

Every FRD follows the same 21 sections:

| # | Section | Built from |
| --- | --- | --- |
| 1 | Document Information | Project title, `--prepared-by` / **Prepared by**, generation date, version 1.0 (Draft) |
| 2 | Stakeholder – Document Signoff Authority | People the sources name as approvers |
| 3 | Team Contact Information | Other named people, with titles and emails as written in the sources |
| 4 | Revision History | The generated draft |
| 5 | Objective | Writer |
| 6 | Overview, with 6.1 Purpose, 6.2 In-Scope, 6.3 Out-of-Scope | Writer and Scope Analyst |
| 7 | End User Roles | Scope Analyst: role, description, responsibilities, access or restrictions |
| 8 | Functional Requirements Summary | `FR-001`...: description, priority, module, acceptance criteria, source |
| 9 | Role-Based Access Summary | Access Analyst: `RBAR-001`... |
| 10 | Detailed Functional Requirements | Use Case Writer: `10.x Use Case` with user story, roles, preconditions, actions, business rules, expected result, exceptions, traceability |
| 11 | Role-Based Access Matrix | Access Analyst |
| 12 | Assumptions (`AS-001`...) and Dependencies (`DEP-001`...) | Specification Analyst |
| 13 | Status Definitions | Specification Analyst |
| 14 | Priority Definitions | Fixed definitions, the source-term mapping and counts per priority |
| 15 | Non-Functional Requirements | Specification Analyst (`NFR-001`...), plus the categories the sources do not cover |
| 16 | Integration Requirements | Specification Analyst, one table per integrated system |
| 17 | UI and Interaction Guidelines | Specification Analyst, including any existing UI standard the sources name |
| 18 | Open Questions and Clarifications | `OQ-001`...: merged from every agent, plus requirements without sources or acceptance criteria and missing sign-off authorities |
| 19 | Requirements Traceability Matrix | Each FR and RBAR with sources, related use cases and status (Confirmed, Pending clarification, No source: to be confirmed) |
| 20 | Acceptance Criteria | Every requirement's criteria |
| 21 | Document Signoff | Approvers, with status Pending |

Two appendices follow: the source documents that the references point to, and any input files that were not used. The web preview shows the same document as the Word file.

Techniques used:

- **Understand first, then retrieve.** The Scope Analyst works from the whole document set (or digests of it, when it exceeds `full_context_words`), so the modules reflect the actual system rather than whatever a few searches returned.
- **Hybrid retrieval.** BM25 catches exact terms (IDs, numbers, jargon) and dense embeddings catch paraphrases; Reciprocal Rank Fusion merges the two rankings.
- **Agent-generated queries.** Each module's search queries are written by the Scope Analyst in the vocabulary of the documents.
- **Coverage check.** Any passage that no requirement cites is re-read, so requirements that retrieval missed are recovered.
- **Grounding and traceability.** Every excerpt carries an ID (such as `D2-001`). Requirements must cite IDs, citations to unknown IDs are removed and flagged, and the FRD ends with a traceability matrix.
- **Structured outputs.** Each agent returns JSON that is validated with Pydantic. Invalid replies are sent back to the model to repair.
- **Critique and refine.** The Reviewer flags problems and only the flagged requirements are rewritten. Conflicts between sources become open questions instead of being decided silently.
- **Deterministic rendering.** The LLM produces content, and code builds the template: section order, numbering, IDs, tables, priority definitions and traceability status.
- **Merged open questions.** Questions raised by any agent are de-duplicated and merged, keeping every related requirement, source and owner.
- **Resilience on free tiers.** Primary/fallback models with exponential backoff on 429s, plus an on-disk cache of validated replies so an interrupted run resumes without spending requests again.

## Project structure

```
SpecForge/
├── specforge/
│   ├── __init__.py          package version
│   ├── __main__.py          CLI: python -m specforge
│   ├── web.py               FastAPI server, job queue and downloads: python -m specforge.web
│   ├── static/index.html    web UI (one file, vanilla JavaScript, no build step)
│   ├── pipeline.py          runs the ten stages and writes the outputs
│   ├── ingest.py            input collection, validation, text extraction and chunking
│   ├── retriever.py         hybrid BM25 + embedding search fused with RRF
│   ├── agents.py            the ten agents, ID assignment and open-question merging
│   ├── prompts.py           prompt catalog loader and CLI: python -m specforge.prompts
│   ├── llm.py               model client: OpenRouter, local Ollama fallback, backoff, JSON repair, cache, cancellation
│   ├── models.py            Pydantic schemas for the FRD and its parts
│   ├── render.py            the 21-section template: Word, Markdown and web preview
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

The tables below are generated from `prompts/` by `sync-readme`, and `check` fails when they drift, so they always match what the application uses. They list every prompt and every version; the full text is shown for the runtime prompts in use, and every other version links to its file. Version 1.0.0 of each original runtime prompt is the text that was in the code when the catalog was created; later versions record each change and why.

<!-- BEGIN GENERATED: prompt catalog. Edit prompts/ and run `python -m specforge.prompts sync-readme`. -->

#### Runtime prompts

These are sent to the model by the application. Templates are filled with `str.format`; fragments are shared instructions inserted into templates. Expand a prompt to read the version in use.

| Prompt | Version | Kind | Purpose | Used by | Output |
| --- | --- | --- | --- | --- | --- |
| `document_reader` | [1.1.0](prompts/runtime/document_reader/1.1.0.toml) | template | For document sets larger than full_context_words, condenses each part into dense notes under fixed FRD headings, keeping excerpt IDs so later agents can still cite sources. Not called when the whole set fits the context budget. | specforge/agents.py: document_reader() | Plain-text notes (LLM.complete) |
| `scope_analyst` | [2.0.0](prompts/runtime/scope_analyst/2.0.0.toml) | template | Establishes the project context from the whole material (or the reader's notes): title, purpose, scope in and out, user roles with responsibilities and access, named people and sign-off authorities, open questions, and the functional modules with search queries in the documents' own vocabulary. | specforge/agents.py: scope_analyst() | JSON validated as models.ProjectContext |
| `specification_analyst` | [1.0.0](prompts/runtime/specification_analyst/1.0.0.toml) | template | Captures the FRD's supporting sections from the whole material: assumptions, dependencies, workflow statuses, non-functional requirements, integrations, UI guidelines and any existing UI standard, plus open questions in these areas. | specforge/agents.py: specification_analyst() | JSON validated as models.Specification |
| `requirements_extractor` | [2.0.0](prompts/runtime/requirements_extractor/2.0.0.toml) | template | Runs once per module on the passages retrieved for it and extracts every supported functional requirement, one behaviour each, with priority, acceptance criteria and citations. | specforge/agents.py: requirements_extractor() | JSON validated as models.RequirementList |
| `coverage_sweep` | [2.0.0](prompts/runtime/coverage_sweep/2.0.0.toml) | template | Re-reads passages that no requirement cites yet and extracts only the requirements the existing set misses, assigning each to a module. | specforge/agents.py: coverage_sweep() | JSON validated as models.RequirementList |
| `reviewer` | [2.0.0](prompts/runtime/reviewer/2.0.0.toml) | template | Reviews the numbered requirement set as a whole and reports duplicates to merge, requirements that are ambiguous, conflicting, incomplete or untestable, and open questions for stakeholders. | specforge/agents.py: reviewer() | JSON validated as models.Review |
| `refiner` | [2.0.0](prompts/runtime/refiner/2.0.0.toml) | template | Rewrites only the requirements the reviewer flagged, using their cited and related passages; issues the sources cannot resolve are explained in notes for stakeholders. | specforge/agents.py: refiner() | JSON validated as models.RequirementList |
| `access_analyst` | [1.0.0](prompts/runtime/access_analyst/1.0.0.toml) | template | Defines role-based access: testable authorization requirements (RBAR) and a matrix of each role's scope, allowed actions and restrictions, consistent with the functional requirements and supported by the sources. | specforge/agents.py: access_analyst() | JSON validated as models.AccessModel |
| `use_case_writer` | [1.0.0](prompts/runtime/use_case_writer/1.0.0.toml) | template | Expands the final functional requirements into detailed use cases: user story, roles, preconditions, step-by-step actions, business rules, expected result, exception handling and source traceability. | specforge/agents.py: use_case_writer() | JSON validated as models.UseCaseList |
| `writer` | [2.0.0](prompts/runtime/writer/2.0.0.toml) | template | Writes the Objective (initiative and document purpose, and how each audience uses the document), the Overview and the Purpose of the Initiative from the project context and final requirements, without adding features. | specforge/agents.py: writer() | JSON validated as models.Summary |
| `json_repair` | [1.0.0](prompts/runtime/json_repair/1.0.0.toml) | template | Sent back to the model when a reply is not valid JSON or does not match the expected schema, asking it to fix the structure without dropping content. | specforge/llm.py: LLM.complete_json() | Corrected JSON in the originally requested shape |
| `grounding` | [1.1.0](prompts/runtime/grounding/1.1.0.toml) | fragment | Shared instruction that keeps every agent tied to the source material: no invented features, values, roles, people or rules; empty fields where the sources are silent; source terminology preserved; conflicts reported as open questions. | Inserted as {grounding} into every agent template except the writer and JSON repair | Text fragment |
| `priority_rules` | [1.0.0](prompts/runtime/priority_rules/1.0.0.toml) | fragment | Shared rule for assigning Critical, High, Medium or Low only when the sources support it, mapping MoSCoW-style and P1-P3 terms, and recording the source wording; otherwise To be confirmed. | Inserted as {priority_rules} into requirements_extractor, coverage_sweep, refiner and access_analyst | Text fragment |
| `requirement_fields` | [2.0.0](prompts/runtime/requirement_fields/2.0.0.toml) | fragment | Shared definition of the fields every extracted requirement must carry: a 3-8 word title, one testable 'The system shall' sentence, actor, priority with its source basis, inputs and outputs, business rules, 2-4 measurable acceptance criteria and at least one source citation. | Inserted as {fields} into requirements_extractor and coverage_sweep | Text fragment |

<details>
<summary><code>document_reader</code> v1.1.0: Agent 1: Document Reader</summary>

**Purpose:** For document sets larger than full_context_words, condenses each part into dense notes under fixed FRD headings, keeping excerpt IDs so later agents can still cite sources. Not called when the whole set fits the context budget.  
**Used by:** specforge/agents.py: document_reader()  
**Variables:** `{part}`, `{parts}`, `{grounding}`, `{excerpts}`  
**Output:** Plain-text notes (LLM.complete)  
**File:** [prompts/runtime/document_reader/1.1.0.toml](prompts/runtime/document_reader/1.1.0.toml)

```text
You are a business analyst reading part {part} of {parts} of a project's source documents.

Write dense notes on everything in this part that matters for a Functional Requirements Document, under these headings:
Purpose and goals; Users, roles and permissions; People, approvers and contacts; Features and behaviours; Business rules and exact values; Priorities; Workflow statuses; Data and records; Integrations and external systems; User interface; Non-functional needs and constraints; Assumptions and dependencies; Explicitly out of scope; Open issues or conflicts.

Put the excerpt IDs in brackets after each point, e.g. [D1-004]. Skip a heading when this part says nothing about it. {grounding}

EXCERPTS:
{excerpts}
```

</details>

<details>
<summary><code>scope_analyst</code> v2.0.0: Agent 2: Scope Analyst</summary>

**Purpose:** Establishes the project context from the whole material (or the reader's notes): title, purpose, scope in and out, user roles with responsibilities and access, named people and sign-off authorities, open questions, and the functional modules with search queries in the documents' own vocabulary.  
**Used by:** specforge/agents.py: scope_analyst()  
**Variables:** `{grounding}`, `{title_hint}`, `{material}`  
**Output:** JSON validated as models.ProjectContext  
**File:** [prompts/runtime/scope_analyst/2.0.0.toml](prompts/runtime/scope_analyst/2.0.0.toml)

```text
You are a senior business analyst preparing a Functional Requirements Document (FRD).
Read the source material below, whatever domain it is from, and establish the project context:

- title and purpose: the system or initiative name, and 2-3 sentences on the business problem, the goal and the expected outcome.
- scope_in and scope_out: the functionality, modules, integrations and workflows the sources include, and those they explicitly exclude.
- roles: every end-user role or external actor that uses the system, with a description, its responsibilities and any access level or restriction the sources state.
- people: named individuals in the sources (authors, approvers, sponsors, team members), with their title and contact details exactly as written. Set signoff_authority to true only when the sources say the person approves or signs off. Never invent names, titles or contact details.
- modules: the functional modules of the system, meaning distinct capability areas named after what the system does in this domain, not after document sections. Use as many as the material needs, usually 3 to 10; a small system may need only 2. Every feature in the material should belong to one module. For each module write 3 or 4 short search queries in the vocabulary of the sources; they will be used to retrieve the evidence for that module's requirements.
- open_questions: conflicting statements, unclear scope or missing information about the above that stakeholders must resolve.

Cite the supporting excerpt IDs in every "sources" list. {grounding}

Return only a JSON object of this shape:
{{
  "title": "system or project name",
  "purpose": "2-3 sentences",
  "scope_in": ["capability in scope"],
  "scope_out": ["item explicitly out of scope"],
  "roles": [{{"role": "role name", "description": "", "responsibilities": [""], "access": "", "sources": ["D1-001"]}}],
  "people": [{{"name": "", "title": "", "email": "", "phone": "", "signoff_authority": false, "sources": ["D1-001"]}}],
  "modules": [{{"name": "module name", "description": "what this module does", "search_queries": ["query"]}}],
  "open_questions": [{{"question": "", "sources": ["D1-001"], "owner": ""}}]
}}
{title_hint}
{material}
```

</details>

<details>
<summary><code>specification_analyst</code> v1.0.0: Agent 3: Specification Analyst</summary>

**Purpose:** Captures the FRD's supporting sections from the whole material: assumptions, dependencies, workflow statuses, non-functional requirements, integrations, UI guidelines and any existing UI standard, plus open questions in these areas.  
**Used by:** specforge/agents.py: specification_analyst()  
**Variables:** `{title}`, `{grounding}`, `{material}`  
**Output:** JSON validated as models.Specification  
**File:** [prompts/runtime/specification_analyst/1.0.0.toml](prompts/runtime/specification_analyst/1.0.0.toml)

```text
You are a senior business analyst completing the supporting sections of the Functional Requirements Document for {title}.
Read the source material below and capture everything it says in these areas. Leave a list empty when the sources say nothing about it.

- assumptions: statements the sources treat as given or assume to be true.
- dependencies: systems, teams, data, decisions, approvals, timelines or budgets the initiative depends on, each with a short description.
- statuses: workflow or record statuses (for example of an order, request or appointment), with a description and the transition or condition that leads to each, where stated.
- non_functional: non-functional requirements, each with a category (Performance, Security, Availability, Scalability, Reliability, Usability, Accessibility, Maintainability, Auditability, Data protection, or another fitting name). Regulatory and compliance obligations belong here. Keep exact targets and never add targets the sources do not give.
- integrations: external systems the solution exchanges data with, with the purpose, data exchanged, direction, authentication, trigger or frequency, error handling and dependency, where the sources say.
- ui_guidelines: user-interface requirements by area (Navigation, Screens, Forms, Tables, Filters, Search, Pagination, Buttons and actions, Role-based visibility, Validation messages, Common behaviour).
- ui_standards: if the sources say to follow an existing application's UI standards or design system, state that in one sentence that names it; otherwise leave it empty.
- open_questions: conflicting statements or missing information in these areas that stakeholders must resolve.

Cite the supporting excerpt IDs in every "sources" list. {grounding}

Return only a JSON object of this shape:
{{
  "assumptions": [{{"assumption": "", "sources": ["D1-001"]}}],
  "dependencies": [{{"dependency": "", "description": "", "sources": []}}],
  "statuses": [{{"status": "", "description": "", "transition": "", "sources": []}}],
  "non_functional": [{{"category": "", "requirement": "", "sources": []}}],
  "integrations": [{{"system": "", "purpose": "", "data_exchanged": "", "direction": "", "authentication": "", "trigger": "", "error_handling": "", "dependency": "", "sources": []}}],
  "ui_guidelines": [{{"area": "", "guideline": "", "sources": []}}],
  "ui_standards": "",
  "open_questions": [{{"question": "", "sources": [], "owner": ""}}]
}}

{material}
```

</details>

<details>
<summary><code>requirements_extractor</code> v2.0.0: Agent 4: Requirements Extractor</summary>

**Purpose:** Runs once per module on the passages retrieved for it and extracts every supported functional requirement, one behaviour each, with priority, acceptance criteria and citations.  
**Used by:** specforge/agents.py: requirements_extractor()  
**Variables:** `{module}`, `{title}`, `{description}`, `{others}`, `{fields}`, `{priority_rules}`, `{grounding}`, `{excerpts}`  
**Output:** JSON validated as models.RequirementList  
**File:** [prompts/runtime/requirements_extractor/2.0.0.toml](prompts/runtime/requirements_extractor/2.0.0.toml)

```text
You are a business analyst writing the functional requirements for the "{module}" module of {title}.

Module scope: {description}
Other modules (leave their requirements to them): {others}

Extract every functional requirement for this module that the source excerpts support. A functional requirement is a behaviour the system must perform. Split compound statements so each requirement covers one behaviour. When several excerpts state the same requirement, or a later comment, email or change request clarifies it, write it once in its clarified form and cite every supporting excerpt. Put authorization conditions (who may view, create, change or approve something) in business_rules.

{fields}

{priority_rules}

{grounding} If the excerpts hold nothing for this module, return an empty list.

Return only a JSON object of this shape:
{{"requirements": [{{"title": "", "description": "The system shall ...", "actor": "", "priority": "To be confirmed", "priority_basis": "", "inputs": [], "outputs": [], "business_rules": [], "acceptance_criteria": [], "sources": ["D1-001"]}}]}}

SOURCE EXCERPTS:
{excerpts}
```

</details>

<details>
<summary><code>coverage_sweep</code> v2.0.0: Agent 5: Coverage Sweep</summary>

**Purpose:** Re-reads passages that no requirement cites yet and extracts only the requirements the existing set misses, assigning each to a module.  
**Used by:** specforge/agents.py: coverage_sweep()  
**Variables:** `{title}`, `{modules}`, `{fields}`, `{priority_rules}`, `{grounding}`, `{existing}`, `{excerpts}`  
**Output:** JSON validated as models.RequirementList  
**File:** [prompts/runtime/coverage_sweep/2.0.0.toml](prompts/runtime/coverage_sweep/2.0.0.toml)

```text
You are a business analyst checking that the FRD for {title} misses nothing.

No requirement cites the excerpts below yet. Many may be background with no requirements in them. Extract only the functional requirements they contain that the existing requirements do not already cover, and assign each to the best-fitting module: {modules}.

{fields}
- module: one of the module names above, spelled exactly.

{priority_rules}

{grounding} Returning an empty list is fine when there is nothing new.

Return only a JSON object of this shape:
{{"requirements": [{{"module": "", "title": "", "description": "The system shall ...", "actor": "", "priority": "To be confirmed", "priority_basis": "", "inputs": [], "outputs": [], "business_rules": [], "acceptance_criteria": [], "sources": ["D1-001"]}}]}}

EXISTING REQUIREMENTS:
{existing}

UNCITED EXCERPTS:
{excerpts}
```

</details>

<details>
<summary><code>reviewer</code> v2.0.0: Agent 6: Reviewer</summary>

**Purpose:** Reviews the numbered requirement set as a whole and reports duplicates to merge, requirements that are ambiguous, conflicting, incomplete or untestable, and open questions for stakeholders.  
**Used by:** specforge/agents.py: reviewer()  
**Variables:** `{title}`, `{requirements}`  
**Output:** JSON validated as models.Review  
**File:** [prompts/runtime/reviewer/2.0.0.toml](prompts/runtime/reviewer/2.0.0.toml)

```text
You are a requirements quality reviewer for the FRD of {title}.

Review the requirements below as a set and report:
1. merges: requirements that describe the same behaviour or rule, even when worded differently or placed in different modules. Name the one to keep and the ones to drop.
2. issues: requirements that are ambiguous (vague words such as "fast", "easy", "appropriate"), conflicting with another requirement, incomplete (missing a rule, limit or outcome), untestable, or whose acceptance criteria are not measurable. Give a concrete note saying what is wrong.
3. open_questions: decisions the stakeholders must make before build, such as conflicting statements the sources do not resolve, missing values and unclear scope. For each give the related requirement IDs, the excerpt IDs it comes from (taken from those requirements' sources), and the owner if the sources name who should decide.

Report only real problems; an empty list is fine.

Return only a JSON object of this shape:
{{"merges": [{{"keep": "FR-001", "drop": ["FR-007"], "reason": ""}}],
 "issues": [{{"req_id": "FR-002", "type": "ambiguous|conflict|incomplete|untestable", "note": ""}}],
 "open_questions": [{{"question": "", "related_requirements": ["FR-001"], "sources": ["D1-004"], "owner": ""}}]}}

REQUIREMENTS:
{requirements}
```

</details>

<details>
<summary><code>refiner</code> v2.0.0: Agent 7: Refiner</summary>

**Purpose:** Rewrites only the requirements the reviewer flagged, using their cited and related passages; issues the sources cannot resolve are explained in notes for stakeholders.  
**Used by:** specforge/agents.py: refiner()  
**Variables:** `{priority_rules}`, `{grounding}`, `{flagged}`, `{excerpts}`  
**Output:** JSON validated as models.RequirementList  
**File:** [prompts/runtime/refiner/2.0.0.toml](prompts/runtime/refiner/2.0.0.toml)

```text
You are a business analyst fixing requirements that a reviewer flagged.

For each requirement below, rewrite it to resolve the reviewer's note using the source excerpts: make it specific, testable and unambiguous, add the missing rule or value, and make its acceptance criteria measurable. Keep its id.
If the sources cannot resolve the issue (for example two sources conflict), keep the best supported wording and put a one-sentence explanation in "notes" so a stakeholder can decide. Do not resolve the conflict yourself.

{priority_rules}

{grounding}

Return only a JSON object of this shape, with one entry per flagged requirement:
{{"requirements": [{{"id": "FR-001", "title": "", "description": "The system shall ...", "actor": "", "priority": "To be confirmed", "priority_basis": "", "inputs": [], "outputs": [], "business_rules": [], "acceptance_criteria": [], "sources": ["D1-001"], "notes": ["..."]}}]}}

FLAGGED REQUIREMENTS (with reviewer notes):
{flagged}

SOURCE EXCERPTS:
{excerpts}
```

</details>

<details>
<summary><code>access_analyst</code> v1.0.0: Agent 8: Access Analyst</summary>

**Purpose:** Defines role-based access: testable authorization requirements (RBAR) and a matrix of each role's scope, allowed actions and restrictions, consistent with the functional requirements and supported by the sources.  
**Used by:** specforge/agents.py: access_analyst()  
**Variables:** `{title}`, `{roles}`, `{requirements}`, `{priority_rules}`, `{grounding}`, `{excerpts}`  
**Output:** JSON validated as models.AccessModel  
**File:** [prompts/runtime/access_analyst/1.0.0.toml](prompts/runtime/access_analyst/1.0.0.toml)

```text
You are a business analyst defining role-based access for the Functional Requirements Document of {title}.

USER ROLES:
{roles}

FUNCTIONAL REQUIREMENTS:
{requirements}

Using the requirements and the source excerpts below, define:
- access_requirements: authorization requirements only, each a testable "The system shall ..." statement about which role may or may not access, view, create, modify, delete, assign or approve something. Do not restate functional behaviour that has no access condition; that is already in the functional requirements. Give its module, priority, priority_basis, 2-3 measurable acceptance criteria and the supporting excerpt IDs.
- matrix: one row per role with its access scope, the actions it is allowed and its restrictions. It must agree with the access requirements and the functional requirements.
- open_questions: access rules the sources leave unclear or contradictory, with the related requirement IDs.

{priority_rules}

Base every entry on what the requirements and excerpts say about roles and permissions; never grant or deny access the sources do not support. {grounding}

Return only a JSON object of this shape:
{{"access_requirements": [{{"requirement": "The system shall ...", "module": "", "priority": "To be confirmed", "priority_basis": "", "acceptance_criteria": [""], "sources": ["D1-001"]}}],
 "matrix": [{{"role": "", "scope": "", "allowed_actions": [""], "restrictions": [""], "sources": ["D1-001"]}}],
 "open_questions": [{{"question": "", "related_requirements": ["FR-001"], "sources": [], "owner": ""}}]}}

SOURCE EXCERPTS:
{excerpts}
```

</details>

<details>
<summary><code>use_case_writer</code> v1.0.0: Agent 9: Use Case Writer</summary>

**Purpose:** Expands the final functional requirements into detailed use cases: user story, roles, preconditions, step-by-step actions, business rules, expected result, exception handling and source traceability.  
**Used by:** specforge/agents.py: use_case_writer()  
**Variables:** `{title}`, `{roles}`, `{statuses}`, `{grounding}`, `{requirements}`, `{excerpts}`  
**Output:** JSON validated as models.UseCaseList  
**File:** [prompts/runtime/use_case_writer/1.0.0.toml](prompts/runtime/use_case_writer/1.0.0.toml)

```text
You are a business analyst writing the detailed use cases of the Functional Requirements Document for {title}.

User roles: {roles}
Workflow statuses: {statuses}

Group the functional requirements below into use cases: one use case per distinct user goal, each covering one or more related requirements. Every requirement must belong to at least one use case. For each use case give:
- name: a short verb phrase, e.g. "Book an appointment".
- module: the module of its requirements.
- user_story: "As a [user role], I want to [action] so that [business outcome]."
- roles: the user roles involved.
- preconditions: what must be true before it can start.
- actions: the user and system interactions, one step per item, in order.
- business_rules: the rules, validations and authorization conditions that apply.
- expected_result: the outcome after successful execution.
- exceptions: how the system behaves when validation fails, access is denied, required information is missing or the operation cannot complete.
- requirements: the IDs of the requirements it covers.
- sources: the excerpt IDs that support it.

Take everything from the requirements and the source excerpts. Where they do not describe a precondition, result or exception, leave it empty instead of inventing one. {grounding}

Return only a JSON object of this shape:
{{"use_cases": [{{"name": "", "module": "", "user_story": "As a ..., I want to ... so that ...", "roles": [""], "preconditions": [""], "actions": ["The user ...", "The system ..."], "business_rules": [""], "expected_result": "", "exceptions": [""], "requirements": ["FR-001"], "sources": ["D1-001"]}}]}}

REQUIREMENTS:
{requirements}

SOURCE EXCERPTS:
{excerpts}
```

</details>

<details>
<summary><code>writer</code> v2.0.0: Agent 10: Writer</summary>

**Purpose:** Writes the Objective (initiative and document purpose, and how each audience uses the document), the Overview and the Purpose of the Initiative from the project context and final requirements, without adding features.  
**Used by:** specforge/agents.py: writer()  
**Variables:** `{title}`, `{purpose}`, `{scope}`, `{requirements}`  
**Output:** JSON validated as models.Summary  
**File:** [prompts/runtime/writer/2.0.0.toml](prompts/runtime/writer/2.0.0.toml)

```text
You are a technical writer finishing the Functional Requirements Document for {title}.

Project purpose: {purpose}
In scope: {scope}

REQUIREMENTS BY MODULE:
{requirements}

Based only on the information above, and adding no new features, write for business readers:
- objective: a paragraph stating the objective of the initiative and the purpose of this document, then a paragraph on how stakeholders, developers, testers, reviewers and approvers will use it. Separate the paragraphs with a blank line.
- overview: a concise overview of the proposed system or initiative and its main capabilities.
- initiative_purpose: the business purpose of the initiative and its expected outcome.

Return only a JSON object of this shape:
{{"objective": "", "overview": "", "initiative_purpose": ""}}
```

</details>

<details>
<summary><code>json_repair</code> v1.0.0: JSON repair</summary>

**Purpose:** Sent back to the model when a reply is not valid JSON or does not match the expected schema, asking it to fix the structure without dropping content.  
**Used by:** specforge/llm.py: LLM.complete_json()  
**Variables:** `{error}`, `{raw}`  
**Output:** Corrected JSON in the originally requested shape  
**File:** [prompts/runtime/json_repair/1.0.0.toml](prompts/runtime/json_repair/1.0.0.toml)

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
<summary><code>grounding</code> v1.1.0: Grounding rule</summary>

**Purpose:** Shared instruction that keeps every agent tied to the source material: no invented features, values, roles, people or rules; empty fields where the sources are silent; source terminology preserved; conflicts reported as open questions.  
**Used by:** Inserted as {grounding} into every agent template except the writer and JSON repair  
**Output:** Text fragment  
**File:** [prompts/runtime/grounding/1.1.0.toml](prompts/runtime/grounding/1.1.0.toml)

```text
Use only the source material. Do not invent features, values, roles, people or rules it does not support. When the sources are silent on something, leave that field empty. Keep the sources' own terminology, and never silently resolve statements that conflict: report them as open questions.
```

</details>

<details>
<summary><code>priority_rules</code> v1.0.0: Priority rules</summary>

**Purpose:** Shared rule for assigning Critical, High, Medium or Low only when the sources support it, mapping MoSCoW-style and P1-P3 terms, and recording the source wording; otherwise To be confirmed.  
**Used by:** Inserted as {priority_rules} into requirements_extractor, coverage_sweep, refiner and access_analyst  
**Output:** Text fragment  
**File:** [prompts/runtime/priority_rules/1.0.0.toml](prompts/runtime/priority_rules/1.0.0.toml)

```text
Priority rules: use "Critical", "High", "Medium" or "Low" only when the sources state or clearly imply the priority, mapping the sources' own scale: critical, blocker or legally required -> Critical; must have, mandatory or P1 -> High; should have or P2 -> Medium; could have, nice to have or P3 -> Low. Otherwise use "To be confirmed". Put the source wording the priority comes from in priority_basis, for example "Must have (BRD 4.2)"; leave it empty for "To be confirmed".
```

</details>

<details>
<summary><code>requirement_fields</code> v2.0.0: Requirement field guide</summary>

**Purpose:** Shared definition of the fields every extracted requirement must carry: a 3-8 word title, one testable 'The system shall' sentence, actor, priority with its source basis, inputs and outputs, business rules, 2-4 measurable acceptance criteria and at least one source citation.  
**Used by:** Inserted as {fields} into requirements_extractor and coverage_sweep  
**Output:** Text fragment  
**File:** [prompts/runtime/requirement_fields/2.0.0.toml](prompts/runtime/requirement_fields/2.0.0.toml)

```text
For each requirement:
- title: 3-8 words.
- description: one "The system shall ..." sentence that is specific, testable, unambiguous and written from a business perspective. Keep exact values from the sources (limits, time windows, amounts, formats) and their terminology. Rewrite informal wording professionally without changing its meaning.
- actor: the user role or external system that triggers or uses it.
- priority and priority_basis: follow the priority rules.
- inputs / outputs: data the behaviour consumes and produces, if the sources say.
- business_rules: rules, validations and authorization conditions that constrain it, if the sources say.
- acceptance_criteria: 2-4 measurable criteria that describe observable system behaviour, in Given/When/Then form where it fits. Each must check this requirement.
- sources: IDs of the excerpts that support it, e.g. ["D1-004"]. At least one.
```

</details>

#### Development prompts

Requests that shaped the current codebase, in order; requests that were later reverted are left out. Version 1.0.0 is the request as originally written (credentials redacted). Later versions are a professional rewrite with the same intent, or the same request restated later. Follow the links, or run `python -m specforge.prompts show <id>`, to read them.

| Prompt | Versions | Status | Date | Request | Outcome |
| --- | --- | --- | --- | --- | --- |
| `dev-01-rag-agent-chain-design` | [1.0.0](prompts/development/dev-01-rag-agent-chain-design/1.0.0.toml), [1.1.0](prompts/development/dev-01-rag-agent-chain-design/1.1.0.toml) | answered | 2026-10-01 | Design a RAG agent chain that generates an FRD | Recommended a hybrid-retrieval, multi-agent design with grounding, citations, schema-validated outputs and a review step; this became SpecForge's architecture. |
| `dev-02-build-on-free-models` | [1.0.0](prompts/development/dev-02-build-on-free-models/1.0.0.toml), [1.1.0](prompts/development/dev-02-build-on-free-models/1.1.0.toml) | implemented | 2026-10-01 | Build the project on free OpenRouter models | The SpecForge package: ingestion, hybrid retriever, seven-agent pipeline, OpenRouter client with fallback, backoff and caching, CLI, and Word/Markdown/JSON export. |
| `dev-03-upload-frontend` | [1.0.0](prompts/development/dev-03-upload-frontend/1.0.0.toml), [1.1.0](prompts/development/dev-03-upload-frontend/1.1.0.toml) | implemented | 2026-10-01 | Add a web frontend for uploading documents | Upload page served by FastAPI, with live progress and FRD preview and download. |
| `dev-04-domain-agnostic` | [1.0.0](prompts/development/dev-04-domain-agnostic/1.0.0.toml), [1.1.0](prompts/development/dev-04-domain-agnostic/1.1.0.toml) | implemented | 2026-10-01 | Make generation domain-agnostic | Modules, roles and search queries are derived from the documents by the Scope Analyst; domain-specific assumptions were removed and the Document Reader and Coverage Sweep agents were added. |
| `dev-05-web-hosting` | [1.0.0](prompts/development/dev-05-web-hosting/1.0.0.toml), [1.1.0](prompts/development/dev-05-web-hosting/1.1.0.toml) | implemented | 2026-10-01 | Host the application as a local web server | `python -m specforge.web` serves the UI and API at http://127.0.0.1:8000 with a background job queue and progress streaming. |
| `dev-06-bug-fixes-corporate-ui` | [1.0.0](prompts/development/dev-06-bug-fixes-corporate-ui/1.0.0.toml), [1.1.0](prompts/development/dev-06-bug-fixes-corporate-ui/1.1.0.toml) | implemented | 2026-10-01 | Fix bugs and restyle the frontend in a corporate style | Fixes to logging, error messages, cache paths, module de-duplication and exports; a navy and slate UI with a three-step flow, stage descriptions, summary figures and dark mode. |
| `dev-07-repository-cleanup` | [1.0.0](prompts/development/dev-07-repository-cleanup/1.0.0.toml), [1.1.0](prompts/development/dev-07-repository-cleanup/1.1.0.toml), [1.2.0](prompts/development/dev-07-repository-cleanup/1.2.0.toml) | implemented | 2026-10-01 | Remove unused code and files | Two cleanup passes: the first removed dead code, caches, test runs and stale outputs; the second removed an unused prompt field, an unused CSS variable, redundant .gitattributes rules, duplicated README text, local reply and embedding caches and test output. |
| `dev-08-model-and-key-update` | [1.0.0](prompts/development/dev-08-model-and-key-update/1.0.0.toml), [1.1.0](prompts/development/dev-08-model-and-key-update/1.1.0.toml) | implemented | 2026-10-01 | Switch the API key and use Qwen as the primary model | Key stored in .env; Qwen primary with Gemma 4 31B and NVIDIA Nemotron fallbacks; FALLBACK_MODEL accepts a comma-separated list. |
| `dev-09-stop-button` | [1.0.0](prompts/development/dev-09-stop-button/1.0.0.toml), [1.1.0](prompts/development/dev-09-stop-button/1.1.0.toml) | implemented | 2026-10-01 | Add a Stop button for long-running jobs | A Stop button appears after 30 seconds, with confirmation; cancelling interrupts in-flight requests and retry waits, and completed steps stay cached for the next run. |
| `dev-10-remove-samples` | [1.0.0](prompts/development/dev-10-remove-samples/1.0.0.toml), [1.1.0](prompts/development/dev-10-remove-samples/1.1.0.toml) | implemented | 2026-10-07 | Remove the bundled sample documents | The samples/ folder was deleted and the README line that referred to it was removed. |
| `dev-11-input-sources` | [1.0.0](prompts/development/dev-11-input-sources/1.0.0.toml) | implemented | 2026-10-08 | Support file, folder and CLI inputs with per-file tracking | Release 1.1.0: a shared input collection, validation before processing, content or path deduplication, per-file status reporting, folder upload and the --check option. |
| `dev-12-git-and-prompt-management` | [1.0.0](prompts/development/dev-12-git-and-prompt-management/1.0.0.toml) | implemented | 2026-10-08 | Add Git version control and prompt management | Release 1.2.0: tagged Git history, a hardened .gitignore, the versioned prompt catalog with its CLI, this development prompt history, the README overhaul and a CHANGELOG. |
| `dev-13-publish-to-github` | [1.0.0](prompts/development/dev-13-publish-to-github/1.0.0.toml), [1.1.0](prompts/development/dev-13-publish-to-github/1.1.0.toml) | implemented | 2026-10-08 | Publish the project to GitHub | Private repository github.com/Ranjit1407/specforge created; main and tags v1.0.0 to v1.2.0 pushed, and later commits pushed the same way. |
| `dev-14-prompt-history-update` | [1.0.0](prompts/development/dev-14-prompt-history-update/1.0.0.toml), [1.1.0](prompts/development/dev-14-prompt-history-update/1.1.0.toml) | implemented | 2026-10-08 | Update the development prompt history | The reverted Hugging Face (Kimi-K3) request and the request that reverted it were removed and the history renumbered; the GitHub publishing request and the second cleanup request were added. |
| `dev-15-frd-template` | [1.0.0](prompts/development/dev-15-frd-template/1.0.0.toml) | implemented | 2026-10-08 | Generate FRDs in the 21-section enterprise template | Release 2.0.0: the 21-section template, Specification Analyst, Access Analyst and Use Case Writer agents, template-scale priorities, FR/RBAR/UC/AS/DEP/NFR/OQ identifiers, merged open questions, traceability status and a server-rendered preview. |
| `dev-16-ollama-fallback` | [1.0.0](prompts/development/dev-16-ollama-fallback/1.0.0.toml), [1.1.0](prompts/development/dev-16-ollama-fallback/1.1.0.toml) | implemented | 2026-10-08 | Add a local Ollama fallback | Release 2.1.0: Ollama fallback after a failed round of OpenRouter models, a permanent switch when the quota is exhausted, Ollama-only mode, JSON mode, per-prompt context sizing and cancellable streaming. |

#### Prompt version history

Every version of every prompt, newest first. Each version links to its file.

| Prompt | Version | Date | Changes |
| --- | --- | --- | --- |
| `document_reader` | [1.1.0](prompts/runtime/document_reader/1.1.0.toml) | 2026-10-08 | Adds headings for permissions, people and approvers, priorities, statuses, UI, and assumptions and dependencies, so large document sets keep everything the template needs (FRD template (dev-15)). |
| `document_reader` | [1.0.0](prompts/runtime/document_reader/1.0.0.toml) | 2026-10-01 | Initial version, moved unchanged from the source code into the prompt catalog. |
| `scope_analyst` | [2.0.0](prompts/runtime/scope_analyst/2.0.0.toml) | 2026-10-08 | MAJOR: also returns user roles with responsibilities and access, named people with sign-off authority and contact details, and open questions; assumptions and constraints move to the specification analyst (FRD template (dev-15)). |
| `scope_analyst` | [1.0.0](prompts/runtime/scope_analyst/1.0.0.toml) | 2026-10-01 | Initial version, moved unchanged from the source code into the prompt catalog. |
| `specification_analyst` | [1.0.0](prompts/runtime/specification_analyst/1.0.0.toml) | 2026-10-08 | Initial version: captures the template's supporting sections from the whole material (FRD template (dev-15)). |
| `requirements_extractor` | [2.0.0](prompts/runtime/requirements_extractor/2.0.0.toml) | 2026-10-08 | MAJOR: replies use the template priority scale with priority_basis (new {priority_rules} placeholder); duplicate statements and later clarifications are merged into one requirement citing every source; authorization conditions go into business rules (FRD template (dev-15)). |
| `requirements_extractor` | [1.0.0](prompts/runtime/requirements_extractor/1.0.0.toml) | 2026-10-01 | Initial version, moved unchanged from the source code into the prompt catalog. |
| `coverage_sweep` | [2.0.0](prompts/runtime/coverage_sweep/2.0.0.toml) | 2026-10-08 | MAJOR: replies use the template priority scale with priority_basis (new {priority_rules} placeholder) (FRD template (dev-15)). |
| `coverage_sweep` | [1.0.0](prompts/runtime/coverage_sweep/1.0.0.toml) | 2026-10-01 | Initial version, moved unchanged from the source code into the prompt catalog. |
| `reviewer` | [2.0.0](prompts/runtime/reviewer/2.0.0.toml) | 2026-10-08 | MAJOR: open questions become objects with related requirement IDs, sources and owner, for the open-questions table; differently worded duplicates must be merged; unmeasurable acceptance criteria are flagged as issues (FRD template (dev-15)). |
| `reviewer` | [1.0.0](prompts/runtime/reviewer/1.0.0.toml) | 2026-10-01 | Initial version, moved unchanged from the source code into the prompt catalog. |
| `refiner` | [2.0.0](prompts/runtime/refiner/2.0.0.toml) | 2026-10-08 | MAJOR: replies use the template priority scale with priority_basis (new {priority_rules} placeholder) and FR-001 style IDs; conflicts must be explained, never resolved (FRD template (dev-15)). |
| `refiner` | [1.0.0](prompts/runtime/refiner/1.0.0.toml) | 2026-10-01 | Initial version, moved unchanged from the source code into the prompt catalog. |
| `access_analyst` | [1.0.0](prompts/runtime/access_analyst/1.0.0.toml) | 2026-10-08 | Initial version: produces the role-based access requirements (authorization rules only) and the role-based access matrix (FRD template (dev-15)). |
| `use_case_writer` | [1.0.0](prompts/runtime/use_case_writer/1.0.0.toml) | 2026-10-08 | Initial version: expands the functional requirements into detailed use cases in the template's structure (FRD template (dev-15)). |
| `writer` | [2.0.0](prompts/runtime/writer/2.0.0.toml) | 2026-10-08 | MAJOR: writes the template's Objective, Overview and Purpose of the Initiative instead of an executive summary and module overviews (FRD template (dev-15)). |
| `writer` | [1.0.0](prompts/runtime/writer/1.0.0.toml) | 2026-10-01 | Initial version, moved unchanged from the source code into the prompt catalog. |
| `json_repair` | [1.0.0](prompts/runtime/json_repair/1.0.0.toml) | 2026-10-01 | Initial version, moved unchanged from the source code into the prompt catalog. |
| `grounding` | [1.1.0](prompts/runtime/grounding/1.1.0.toml) | 2026-10-08 | Adds people to what must not be invented, asks to keep the sources' terminology, and requires conflicts to be reported as open questions instead of resolved silently (FRD template (dev-15)). |
| `grounding` | [1.0.0](prompts/runtime/grounding/1.0.0.toml) | 2026-10-01 | Initial version, moved unchanged from the source code into the prompt catalog. |
| `priority_rules` | [1.0.0](prompts/runtime/priority_rules/1.0.0.toml) | 2026-10-08 | Initial version: the template's priority scale, stored once and shared by every agent that assigns priorities (FRD template (dev-15)). |
| `requirement_fields` | [2.0.0](prompts/runtime/requirement_fields/2.0.0.toml) | 2026-10-08 | MAJOR: priority moves to the template scale with a priority_basis field (rules in the priority_rules fragment); descriptions must be unambiguous and business-focused; acceptance criteria must be measurable and observable (FRD template (dev-15)). |
| `requirement_fields` | [1.0.0](prompts/runtime/requirement_fields/1.0.0.toml) | 2026-10-01 | Initial version, moved unchanged from the source code into the prompt catalog. |
| `dev-01-rag-agent-chain-design` | [1.1.0](prompts/development/dev-01-rag-agent-chain-design/1.1.0.toml) | 2026-10-08 | Rewritten as a professional prompt with the same intent: names the deliverables (architecture, retrieval approach, accuracy techniques, tool choices) and what the FRD must contain. |
| `dev-01-rag-agent-chain-design` | [1.0.0](prompts/development/dev-01-rag-agent-chain-design/1.0.0.toml) | 2026-10-01 | Original request as written. |
| `dev-02-build-on-free-models` | [1.1.0](prompts/development/dev-02-build-on-free-models/1.1.0.toml) | 2026-10-08 | Rewritten professionally with the same intent: names the models by ID, keeps the key in .env instead of the prompt, and lists the deliverables and the handling of free-tier rate limits. |
| `dev-02-build-on-free-models` | [1.0.0](prompts/development/dev-02-build-on-free-models/1.0.0.toml) | 2026-10-01 | Original request as written; the API key it contained is redacted. |
| `dev-03-upload-frontend` | [1.1.0](prompts/development/dev-03-upload-frontend/1.1.0.toml) | 2026-10-08 | Rewritten professionally with the same intent: specifies validation, progress, results, downloads and error handling, and reuse of the existing pipeline. |
| `dev-03-upload-frontend` | [1.0.0](prompts/development/dev-03-upload-frontend/1.0.0.toml) | 2026-10-01 | Original request as written. |
| `dev-04-domain-agnostic` | [1.1.0](prompts/development/dev-04-domain-agnostic/1.1.0.toml) | 2026-10-08 | Rewritten professionally with the same intent: states what must be derived from the documents, what must be removed, and how to verify it. |
| `dev-04-domain-agnostic` | [1.0.0](prompts/development/dev-04-domain-agnostic/1.0.0.toml) | 2026-10-01 | Original request as written. |
| `dev-05-web-hosting` | [1.1.0](prompts/development/dev-05-web-hosting/1.1.0.toml) | 2026-10-08 | Rewritten professionally with the same intent: defines the single start command, background jobs, progress streaming, per-job storage and documentation. |
| `dev-05-web-hosting` | [1.0.0](prompts/development/dev-05-web-hosting/1.0.0.toml) | 2026-10-01 | Original request as written. |
| `dev-06-bug-fixes-corporate-ui` | [1.1.0](prompts/development/dev-06-bug-fixes-corporate-ui/1.1.0.toml) | 2026-10-08 | Rewritten professionally with the same intent: lists what the bug review covers and the concrete goals of the redesign. |
| `dev-06-bug-fixes-corporate-ui` | [1.0.0](prompts/development/dev-06-bug-fixes-corporate-ui/1.0.0.toml) | 2026-10-01 | Original request as written. |
| `dev-07-repository-cleanup` | [1.2.0](prompts/development/dev-07-repository-cleanup/1.2.0.toml) | 2026-10-08 | Restated on 2026-10-08 as a full specification that also covers duplicate and generated files, unused dependencies, configurations and assets, and outdated prompts and documentation. Recorded as written, since it is already implementation-ready. |
| `dev-07-repository-cleanup` | [1.1.0](prompts/development/dev-07-repository-cleanup/1.1.0.toml) | 2026-10-08 | Rewritten professionally with the same intent: separates unused code from generated files, says what must be kept, and requires verification. |
| `dev-07-repository-cleanup` | [1.0.0](prompts/development/dev-07-repository-cleanup/1.0.0.toml) | 2026-10-01 | Original request as written. It was repeated on 2026-10-07 as "remove unwanted files and unused files"; stored once to avoid a duplicate. |
| `dev-08-model-and-key-update` | [1.1.0](prompts/development/dev-08-model-and-key-update/1.1.0.toml) | 2026-10-08 | Rewritten professionally with the same intent: keeps the key out of the prompt and code, and asks for a cross-provider fallback chain and a live test. |
| `dev-08-model-and-key-update` | [1.0.0](prompts/development/dev-08-model-and-key-update/1.0.0.toml) | 2026-10-01 | Original request as written; the API key it contained is redacted. |
| `dev-09-stop-button` | [1.1.0](prompts/development/dev-09-stop-button/1.1.0.toml) | 2026-10-08 | Rewritten professionally with the same intent: defines when the button appears, confirmation, how quickly it must stop, and what happens to completed work and queued jobs. |
| `dev-09-stop-button` | [1.0.0](prompts/development/dev-09-stop-button/1.0.0.toml) | 2026-10-01 | Original request as written. |
| `dev-10-remove-samples` | [1.1.0](prompts/development/dev-10-remove-samples/1.1.0.toml) | 2026-10-08 | Rewritten professionally with the same intent: includes updating documentation that refers to the samples. |
| `dev-10-remove-samples` | [1.0.0](prompts/development/dev-10-remove-samples/1.0.0.toml) | 2026-10-07 | Original request as written. |
| `dev-11-input-sources` | [1.0.0](prompts/development/dev-11-input-sources/1.0.0.toml) | 2026-10-08 | Recorded as written: it was already a complete, implementation-ready specification. |
| `dev-12-git-and-prompt-management` | [1.0.0](prompts/development/dev-12-git-and-prompt-management/1.0.0.toml) | 2026-10-08 | Recorded as written: it was already a complete, implementation-ready specification. |
| `dev-13-publish-to-github` | [1.1.0](prompts/development/dev-13-publish-to-github/1.1.0.toml) | 2026-10-08 | Rewritten professionally with the same intent: adds the pre-push secret check, the visibility default, pushing tags, upstream tracking for later pushes, and what to report. |
| `dev-13-publish-to-github` | [1.0.0](prompts/development/dev-13-publish-to-github/1.0.0.toml) | 2026-10-08 | Original request as written. The later request "push it to github" (2026-10-08) asked to publish new commits to the same repository; it is recorded here instead of as a duplicate prompt. |
| `dev-14-prompt-history-update` | [1.1.0](prompts/development/dev-14-prompt-history-update/1.1.0.toml) | 2026-10-08 | Rewritten professionally with the same intent: defines which prompts count as reverted, how to number and de-duplicate new entries, and the validation to run. |
| `dev-14-prompt-history-update` | [1.0.0](prompts/development/dev-14-prompt-history-update/1.0.0.toml) | 2026-10-08 | Original request as written. |
| `dev-15-frd-template` | [1.0.0](prompts/development/dev-15-frd-template/1.0.0.toml) | 2026-10-08 | Recorded as written: it was already a complete, implementation-ready specification. |
| `dev-16-ollama-fallback` | [1.1.0](prompts/development/dev-16-ollama-fallback/1.1.0.toml) | 2026-10-08 | Rewritten professionally with the same intent: defines when the fallback applies, the configuration, and how context limits, JSON output and cancellation must behave. |
| `dev-16-ollama-fallback` | [1.0.0](prompts/development/dev-16-ollama-fallback/1.0.0.toml) | 2026-10-08 | Original request as written. |

<!-- END GENERATED: prompt catalog -->

## Git workflow

### Repository setup

The code is hosted at https://github.com/Ranjit1407/specforge (a private repository: ask the owner for access). Work happens on `main`, and each release is tagged (`v1.0.0` to `v2.1.0`, listed in [CHANGELOG.md](CHANGELOG.md)). After cloning, `origin` already points at GitHub, so publishing changes is:

```bash
git push                 # commits on main
git push origin --tags   # new release tags
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
git commit -m "Release 2.2.0"
git tag -a v2.2.0 -m "SpecForge 2.2.0: <one-line summary>"
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
| List and inspect releases | `git tag -n`, `git show v2.1.0` |
| Compare two releases | `git diff v2.0.0 v2.1.0 --stat` |
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

OpenRouter free models allow 50 requests per day (1,000 per day once the account has $10 of credit). A typical run makes roughly `modules + 8` calls, about 12 to 20. When every model is rate-limited, the client uses the local Ollama model if one is configured, and otherwise backs off before giving up; retrying resumes from cached steps.

The web server keeps jobs in memory. Uploaded files and results are stored under `runs/<job-id>/`, but the job list resets when the server restarts.
