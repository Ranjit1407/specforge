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

<!-- END GENERATED: prompt catalog -->

## Free-tier limits

OpenRouter free models allow 50 requests per day (1,000 per day once the account has $10 of credit). A typical run makes roughly 8 to 15 calls. When every model is rate-limited, the client backs off before giving up; retrying resumes from cached steps.

To use other models, set PRIMARY_MODEL and FALLBACK_MODEL in .env to any OpenRouter model ID.

The web server keeps jobs in memory. Uploaded files and results are stored under `runs/<job-id>/`, but the job list resets when the server restarts.
