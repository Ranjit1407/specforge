from dataclasses import dataclass
from datetime import date
from pathlib import Path

from docx import Document
from docx.shared import Pt

from .models import FRD, Requirement


@dataclass
class Block:
    kind: str  # heading | para | bullets | numbered | table | note
    text: str = ""
    level: int = 1
    items: list | None = None
    headers: list[str] | None = None


def _sources(req: Requirement, chunk_cites: dict[str, str]) -> str:
    return "; ".join(f"{chunk_cites.get(s, s)} [{s}]" for s in req.sources) or "none"


def build_blocks(frd: FRD, chunk_cites: dict[str, str]) -> list[Block]:
    ctx, b = frd.context, []
    b.append(Block("heading", f"Functional Requirements Document: {ctx.title}", 0))
    b.append(Block("para", f"Generated {date.today().isoformat()} by SpecForge · {len(frd.requirements)} requirements "
                           f"from {len(frd.sources)} source documents"))

    b.append(Block("heading", "1. Introduction", 1))
    b.append(Block("heading", "1.1 Purpose", 2))
    b.append(Block("para", ctx.purpose))
    b.append(Block("heading", "1.2 Executive Summary", 2))
    b += [Block("para", p.strip()) for p in frd.summary.executive_summary.split("\n\n") if p.strip()]
    b.append(Block("heading", "1.3 Scope", 2))
    b.append(Block("para", "In scope:"))
    b.append(Block("bullets", items=ctx.scope_in or ["Not stated in sources."]))
    b.append(Block("para", "Out of scope:"))
    b.append(Block("bullets", items=ctx.scope_out or ["Not stated in sources."]))
    b.append(Block("heading", "1.4 Stakeholders and User Roles", 2))
    b.append(Block("table", headers=["Role", "Needs"], items=[[s.role, s.description] for s in ctx.stakeholders]))
    b.append(Block("heading", "1.5 Assumptions", 2))
    b.append(Block("bullets", items=ctx.assumptions or ["None stated in sources."]))
    b.append(Block("heading", "1.6 Constraints", 2))
    b.append(Block("bullets", items=ctx.constraints or ["None stated in sources."]))

    b.append(Block("heading", "2. Functional Requirements", 1))
    b.append(Block("table", headers=["Module", "Requirements", "Must", "Should", "Could"], items=[
        [m.name, str(len(rs := [r for r in frd.requirements if r.module == m.name])),
         *(str(sum(r.priority == p for r in rs)) for p in ("Must", "Should", "Could"))]
        for m in ctx.modules]))
    for i, module in enumerate(ctx.modules, 1):
        reqs = [r for r in frd.requirements if r.module == module.name]
        b.append(Block("heading", f"2.{i} {module.name}", 2))
        b.append(Block("para", frd.summary.module_overviews.get(module.name) or module.description))
        if not reqs:
            b.append(Block("para", "No requirements were found in the sources for this module."))
        for r in reqs:
            b.append(Block("heading", f"{r.id}: {r.title}", 3))
            b.append(Block("table", headers=["Priority", "Actor"], items=[[r.priority, r.actor or "-"]]))
            b.append(Block("para", r.description))
            for label, values in (("Inputs", r.inputs), ("Outputs", r.outputs), ("Business rules", r.business_rules)):
                if values:
                    b.append(Block("para", f"{label}:"))
                    b.append(Block("bullets", items=values))
            b.append(Block("para", "Acceptance criteria:"))
            b.append(Block("numbered", items=r.acceptance_criteria or ["To be defined."]))
            b.append(Block("para", f"Sources: {_sources(r, chunk_cites)}"))
            for note in r.notes:
                b.append(Block("note", f"Review note: {note}"))

    b.append(Block("heading", "3. Open Questions", 1))
    b.append(Block("numbered", items=frd.open_questions or ["None raised."]))

    b.append(Block("heading", "4. Traceability Matrix", 1))
    b.append(Block("table", headers=["Requirement", "Title", "Priority", "Sources"], items=[
        [r.id, r.title, r.priority, _sources(r, chunk_cites)] for r in frd.requirements]))

    b.append(Block("heading", "Appendix A. Source Documents", 1))
    b.append(Block("table", headers=["ID", "Document"], items=[[k, v] for k, v in frd.sources.items()]))
    return b


def _cell(text: str) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def to_markdown(blocks: list[Block]) -> str:
    out: list[str] = []
    for blk in blocks:
        if blk.kind == "heading":
            out.append(f"{'#' * (blk.level + 1)} {blk.text}")
        elif blk.kind == "para":
            out.append(blk.text)
        elif blk.kind == "note":
            out.append(f"> {blk.text}")
        elif blk.kind == "bullets":
            out.append("\n".join(f"- {x}" for x in blk.items))
        elif blk.kind == "numbered":
            out.append("\n".join(f"{n}. {x}" for n, x in enumerate(blk.items, 1)))
        elif blk.kind == "table":
            rows = [blk.headers, ["---"] * len(blk.headers), *blk.items]
            out.append("\n".join("| " + " | ".join(_cell(c) for c in row) + " |" for row in rows))
    return "\n\n".join(out) + "\n"


def to_docx(blocks: list[Block], path: Path) -> None:
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10.5)
    for blk in blocks:
        if blk.kind == "heading":
            doc.add_heading(blk.text, level=blk.level)
        elif blk.kind == "para":
            doc.add_paragraph(blk.text)
        elif blk.kind == "note":
            doc.add_paragraph().add_run(blk.text).italic = True
        elif blk.kind == "bullets":
            for item in blk.items:
                doc.add_paragraph(str(item), style="List Bullet")
        elif blk.kind == "numbered":
            # Word's "List Number" style keeps counting across lists, so number explicitly.
            for n, item in enumerate(blk.items, 1):
                doc.add_paragraph(f"{n}. {item}")
        elif blk.kind == "table":
            table = doc.add_table(rows=1, cols=len(blk.headers))
            table.style = "Table Grid"
            for cell, h in zip(table.rows[0].cells, blk.headers):
                cell.text = h
                cell.paragraphs[0].runs[0].bold = True
            for row in blk.items:
                for cell, value in zip(table.add_row().cells, row):
                    cell.text = str(value)
            doc.add_paragraph()
    doc.save(path)
