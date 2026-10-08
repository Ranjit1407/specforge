"""Versioned prompt catalog.

Every prompt lives in prompts/<category>/<id>/<version>.toml at the repository root:

- runtime:     prompts the application sends to the model (templates and shared fragments).
- development: the requests used to build SpecForge, kept for history and reuse.

The highest semantic version of a prompt is the one in use; older versions stay on disk as history.
Run `python -m specforge.prompts --help` for the maintenance commands.
"""
import argparse
import datetime
import json
import re
import string
import sys
import tomllib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT / "prompts"
README = ROOT / "README.md"
CATEGORIES = ("runtime", "development")
KINDS = {"runtime": ("template", "fragment"), "development": ("development",)}
REQUIRED = ("id", "version", "title", "kind", "date", "purpose", "changes", "template")
README_BEGIN = "<!-- BEGIN GENERATED: prompt catalog. Edit prompts/ and run `python -m specforge.prompts sync-readme`. -->"
README_END = "<!-- END GENERATED: prompt catalog -->"
SECRET_PATTERNS = [re.compile(p) for p in (
    r"sk-or-v1-[0-9a-f]{16,}", r"sk-ant-[A-Za-z0-9_-]{16,}", r"sk-[A-Za-z0-9]{32,}", r"hf_[A-Za-z0-9]{20,}",
    r"gh[pousr]_[A-Za-z0-9]{20,}", r"AKIA[0-9A-Z]{16}")]
TODO_CHANGES = "TODO: describe what changed and why."


class PromptError(LookupError):
    pass


@dataclass(frozen=True)
class Prompt:
    id: str
    version: str
    title: str
    kind: str
    date: str
    purpose: str
    changes: str
    template: str
    category: str
    path: Path
    status: str = "active"
    used_by: str = ""
    variables: tuple[str, ...] = ()
    output: str = ""
    outcome: str = ""

    @property
    def semver(self) -> tuple[int, int, int]:
        return parse_version(self.version)

    def render(self, **values) -> str:
        if self.kind != "template":
            raise PromptError(f"{self.id} is a {self.kind}, not a template")
        return self.template.format(**values)


def parse_version(version: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    if not match:
        raise ValueError(f"{version!r} is not a MAJOR.MINOR.PATCH version")
    return tuple(int(n) for n in match.groups())


def placeholders(template: str) -> set[str]:
    return {name.split(".")[0].split("[")[0] for _, name, _, _ in string.Formatter().parse(template) if name}


def _load_file(path: Path, category: str) -> Prompt:
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    missing = [k for k in REQUIRED if k not in data]
    if missing:
        raise PromptError(f"{path.relative_to(ROOT)}: missing {', '.join(missing)}")
    known = {f for f in Prompt.__dataclass_fields__ if f not in ("category", "path")}
    values = {k: (tuple(v) if k == "variables" else str(v) if isinstance(v, datetime.date) else v)
              for k, v in data.items() if k in known}
    return Prompt(**values, category=category, path=path)


@lru_cache(maxsize=1)
def _catalog() -> dict[str, list[Prompt]]:
    """All prompts by id, each list sorted from oldest to newest version."""
    found: dict[str, list[Prompt]] = {}
    for category in CATEGORIES:
        for path in sorted((PROMPTS_DIR / category).glob("*/*.toml")):
            prompt = _load_file(path, category)
            found.setdefault(prompt.id, []).append(prompt)
    for versions in found.values():
        versions.sort(key=lambda p: p.semver)
    return found


def reload() -> None:
    _catalog.cache_clear()


def history(prompt_id: str) -> list[Prompt]:
    try:
        return list(_catalog()[prompt_id])
    except KeyError:
        raise PromptError(f"unknown prompt {prompt_id!r}; see `python -m specforge.prompts list`") from None


def get(prompt_id: str, version: str | None = None) -> Prompt:
    versions = history(prompt_id)
    if version is None:
        return versions[-1]
    for prompt in versions:
        if prompt.version == version:
            return prompt
    raise PromptError(f"{prompt_id} has no version {version}; available: {', '.join(p.version for p in versions)}")


def render(prompt_id: str, **values) -> str:
    """Fills the current version of a template prompt."""
    return get(prompt_id).render(**values)


def text(prompt_id: str) -> str:
    """The current text of a fragment, for inserting into templates."""
    return get(prompt_id).template


def latest(category: str | None = None) -> list[Prompt]:
    return [v[-1] for v in _catalog().values() if category in (None, v[-1].category)]


def search(query: str) -> list[Prompt]:
    """Every version whose id, title, purpose or text contains all words of the query."""
    words = query.lower().split()
    hits = []
    for versions in _catalog().values():
        for p in versions:
            haystack = " ".join((p.id, p.title, p.purpose, p.changes, p.template, p.outcome)).lower()
            if all(w in haystack for w in words):
                hits.append(p)
    return hits


# --- Validation --------------------------------------------------------------------------------

def problems() -> list[str]:
    """Everything that should block a commit: broken files, duplicates, secrets, a stale README."""
    issues: list[str] = []
    unreadable = False
    for category in CATEGORIES:
        for path in sorted((PROMPTS_DIR / category).glob("*/*.toml")):
            rel = path.relative_to(ROOT).as_posix()
            raw = path.read_text(encoding="utf-8")
            for pattern in SECRET_PATTERNS:
                if pattern.search(raw):
                    issues.append(f"{rel}: looks like it contains a secret ({pattern.pattern}); redact it")
            try:
                p = _load_file(path, category)
                parse_version(p.version)
            except (PromptError, ValueError, tomllib.TOMLDecodeError) as e:
                issues.append(f"{rel}: {e}")
                unreadable = True
                continue
            if p.id != path.parent.name:
                issues.append(f"{rel}: id {p.id!r} does not match its folder")
            if p.version != path.stem:
                issues.append(f"{rel}: version {p.version} does not match its file name")
            if p.kind not in KINDS[category]:
                issues.append(f"{rel}: kind {p.kind!r} is not allowed in {category}/ (use {', '.join(KINDS[category])})")
            if not p.changes.strip() or p.changes == TODO_CHANGES:
                issues.append(f"{rel}: describe what changed and why in 'changes'")
            if p.kind == "template" and placeholders(p.template) != set(p.variables):
                issues.append(f"{rel}: variables {sorted(p.variables)} do not match the template's "
                              f"placeholders {sorted(placeholders(p.template))}")
    if unreadable:
        return issues
    reload()
    for prompt_id, versions in _catalog().items():
        # A new version must change the text; returning to an older text later (a rollback) is allowed.
        for before, after in zip(versions, versions[1:]):
            if before.template == after.template:
                issues.append(f"{prompt_id}: version {after.version} has the same text as {before.version}")
    readme = README.read_text(encoding="utf-8")
    try:
        if _readme_with_section(readme) != readme:
            issues.append("README.md prompt section is out of date; run `python -m specforge.prompts sync-readme`")
    except PromptError as e:
        issues.append(str(e))
    return issues


# --- Creating new versions ---------------------------------------------------------------------

def _toml_value(value) -> str:
    if isinstance(value, (list, tuple)):
        return json.dumps(list(value), ensure_ascii=False)
    return json.dumps(str(value), ensure_ascii=False)


def to_toml(fields: dict) -> str:
    """Serialises a prompt; the template is written as a TOML literal string so it needs no escaping."""
    template = fields["template"]
    if "'''" in template:
        raise PromptError("templates cannot contain ''' (TOML literal string delimiter)")
    lines = [f"{k} = {_toml_value(v)}" for k, v in fields.items() if k != "template" and v not in ("", (), [], None)]
    return "\n".join(lines) + f"\n\ntemplate = '''\n{template}'''\n"


def bump(prompt_id: str, part: str = "minor") -> Path:
    """Copies the current version of a prompt to the next version, ready to edit."""
    current = get(prompt_id)
    major, minor, patch = current.semver
    version = {"major": f"{major + 1}.0.0", "minor": f"{major}.{minor + 1}.0", "patch": f"{major}.{minor}.{patch + 1}"}[part]
    target = current.path.with_name(f"{version}.toml")
    if target.exists():
        raise PromptError(f"{target.relative_to(ROOT)} already exists")
    data = tomllib.loads(current.path.read_text(encoding="utf-8"))
    data.update(version=version, date=datetime.date.today().isoformat(), changes=TODO_CHANGES)
    order = ["id", "version", "title", "kind", "status", "date", "purpose", "used_by", "variables", "output", "outcome", "changes"]
    fields = {k: data[k] for k in order if k in data} | {k: v for k, v in data.items() if k not in order}
    target.write_text(to_toml(fields), encoding="utf-8")
    reload()
    return target


# --- README ------------------------------------------------------------------------------------

def _cell(value: str) -> str:
    return " ".join(str(value).split()).replace("|", "\\|")


def _fenced(body: str) -> str:
    fence = "`" * max(3, max((len(m) for m in re.findall(r"`+", body)), default=0) + 1)
    return f"{fence}text\n{body}\n{fence}"


def _details(p: Prompt, previous: list[Prompt]) -> str:
    meta = [f"**Purpose:** {p.purpose}"]
    if p.used_by:
        meta.append(f"**Used by:** {p.used_by}")
    if p.variables:
        meta.append("**Variables:** " + ", ".join(f"`{{{v}}}`" for v in p.variables))
    if p.output:
        meta.append(f"**Output:** {p.output}")
    if p.outcome:
        meta.append(f"**Outcome:** {p.outcome}")
    meta.append(f"**File:** `{p.path.relative_to(ROOT).as_posix()}`")
    parts = [f"<details>\n<summary><code>{p.id}</code> v{p.version}: {p.title}</summary>\n", "  \n".join(meta), "", _fenced(p.template)]
    for old in reversed(previous):
        parts += ["", f"Previous version {old.version} ({old.date}): {old.changes}", "", _fenced(old.template)]
    return "\n".join(parts) + "\n\n</details>"


def readme_section() -> str:
    # Templates in pipeline order, then fragments.
    pipeline_order = ["document_reader", "scope_analyst", "specification_analyst", "requirements_extractor",
                      "coverage_sweep", "reviewer", "refiner", "access_analyst", "use_case_writer", "writer"]
    runtime = sorted(latest("runtime"), key=lambda p: (
        p.kind != "template", pipeline_order.index(p.id) if p.id in pipeline_order else len(pipeline_order), p.id))
    development = sorted(latest("development"), key=lambda p: p.id)
    out = [README_BEGIN, "", "#### Runtime prompts", "",
           "These are sent to the model by the application. Templates are filled with `str.format`; fragments are "
           "shared instructions inserted into templates.", "",
           "| Prompt | Version | Kind | Purpose | Used by | Output |", "| --- | --- | --- | --- | --- | --- |"]
    out += [f"| `{p.id}` | {p.version} | {p.kind} | {_cell(p.purpose)} | {_cell(p.used_by)} | {_cell(p.output)} |" for p in runtime]
    out += [""] + [_details(p, history(p.id)[:-1]) + "\n" for p in runtime]
    if development:
        out += ["#### Development prompts", "",
                "Requests that shaped the current codebase, in order; requests that were later reverted are left out. "
                "Version 1.0.0 is the request as originally written (credentials redacted). Later versions are a "
                "professional rewrite with the same intent, or the same request restated later; each version's change "
                "note says which.", "",
                "| Prompt | Version | Status | Date | Request |", "| --- | --- | --- | --- | --- |"]
        out += [f"| `{p.id}` | {p.version} | {p.status} | {history(p.id)[0].date} | {_cell(p.title)} |" for p in development]
        out += [""] + [_details(p, history(p.id)[:-1]) + "\n" for p in development]
    out += ["#### Prompt version history", "", "| Prompt | Version | Date | Changes |", "| --- | --- | --- | --- |"]
    for p in runtime + development:
        out += [f"| `{v.id}` | {v.version} | {v.date} | {_cell(v.changes)} |" for v in reversed(history(p.id))]
    out += ["", README_END]
    return "\n".join(out)


def _readme_with_section(readme: str) -> str:
    if README_BEGIN not in readme or README_END not in readme:
        raise PromptError("README.md has no generated prompt section markers")
    start = readme.index(README_BEGIN)
    end = readme.index(README_END) + len(README_END)
    return readme[:start] + readme_section() + readme[end:]


def sync_readme() -> bool:
    current = README.read_text(encoding="utf-8")
    updated = _readme_with_section(current)
    if updated != current:
        README.write_text(updated, encoding="utf-8")
    return updated != current


# --- CLI ---------------------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    from rich.console import Console
    from rich.table import Table

    parser = argparse.ArgumentParser(prog="python -m specforge.prompts", description="Browse and maintain SpecForge prompts.")
    sub = parser.add_subparsers(dest="command", required=True)
    p_list = sub.add_parser("list", help="current version of every prompt")
    p_list.add_argument("--category", choices=CATEGORIES)
    p_show = sub.add_parser("show", help="print a prompt's text and metadata")
    p_show.add_argument("id")
    p_show.add_argument("--version")
    p_hist = sub.add_parser("history", help="every version of a prompt with its change notes")
    p_hist.add_argument("id")
    p_search = sub.add_parser("search", help="find prompts whose id, title, purpose or text contain all the words")
    p_search.add_argument("query", nargs="+")
    p_bump = sub.add_parser("bump", help="copy the current version to a new version file to edit")
    p_bump.add_argument("id")
    p_bump.add_argument("--part", choices=("major", "minor", "patch"), default="minor")
    sub.add_parser("check", help="validate every prompt file and the README section; exit 1 on problems")
    sub.add_parser("sync-readme", help="regenerate the prompt section of README.md")
    args = parser.parse_args(argv)
    console = Console(emoji=False, highlight=False)

    try:
        if args.command in ("list", "search"):
            rows = latest(args.category) if args.command == "list" else search(" ".join(args.query))
            table = Table(show_lines=False)
            for column in ("Prompt", "Version", "Category", "Kind", "Title"):
                table.add_column(column, overflow="fold")
            for p in sorted(rows, key=lambda p: (p.category != "runtime", p.id, p.semver)):
                table.add_row(p.id, p.version, p.category, p.kind, p.title)
            console.print(table if rows else "No matching prompts.")
        elif args.command == "show":
            p = get(args.id, args.version)
            console.print(f"[bold]{p.id}[/] v{p.version} ({p.category}, {p.kind}) {p.date}\n{p.title}\n\n{p.purpose}")
            if p.variables:
                console.print("Variables: " + ", ".join(p.variables))
            console.print(f"File: {p.path.relative_to(ROOT).as_posix()}\n")
            console.print(p.template, markup=False)
        elif args.command == "history":
            table = Table()
            for column in ("Version", "Date", "Changes"):
                table.add_column(column, overflow="fold")
            for p in reversed(history(args.id)):
                table.add_row(p.version, p.date, p.changes)
            console.print(table)
        elif args.command == "bump":
            path = bump(args.id, args.part)
            console.print(f"Created {path.relative_to(ROOT).as_posix()}. Edit the template and 'changes', then run "
                          "`python -m specforge.prompts check` and `sync-readme`.")
        elif args.command == "check":
            issues = problems()
            for issue in issues:
                console.print(f"[red]x[/] {issue}", markup=True)
            count = sum(len(v) for v in _catalog().values()) if not issues else 0
            console.print(f"{len(issues)} problem(s)" if issues else f"OK: {len(_catalog())} prompts, {count} versions")
            return 1 if issues else 0
        elif args.command == "sync-readme":
            console.print("README.md updated." if sync_readme() else "README.md already up to date.")
    except PromptError as e:
        console.print(f"Error: {e}", markup=False)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
