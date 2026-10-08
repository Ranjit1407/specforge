"""Builds the FRD document from the model output.

The LLM produces content; this module owns the template: section order and numbering, tables, identifiers,
priority definitions and traceability status. The same blocks render to Word, Markdown and the web preview.
"""
import html
import re
from dataclasses import dataclass
from pathlib import Path

from docx import Document
from docx.shared import Pt

from .models import FRD, NOT_SPECIFIED, PRIORITIES, OpenQuestion

NA = NOT_SPECIFIED
NFR_CATEGORIES = ["Performance", "Security", "Availability", "Scalability", "Reliability", "Usability",
                  "Accessibility", "Maintainability", "Auditability", "Data protection"]
UI_AREAS = ["Navigation", "Screens", "Forms", "Tables", "Filters", "Search", "Pagination", "Buttons and actions",
            "Role-based visibility", "Validation messages", "Common behaviour"]
PRIORITY_DEFINITIONS = {
    "Critical": "Essential. The solution cannot go live, or cannot meet a legal or regulatory obligation, without it.",
    "High": "Required for the initial release; a core business process depends on it.",
    "Medium": "Important, but can follow the core functionality or has an acceptable workaround.",
    "Low": "Desirable; can be deferred without significant business impact.",
    "To be confirmed": "The source material does not state a priority. It must be confirmed by stakeholders.",
}


@dataclass
class Block:
    kind: str  # heading | para | label | bullets | numbered | table | note
    text: str = ""
    level: int = 1
    items: list | None = None
    headers: list[str] | None = None


def _or_na(value) -> str:
    if isinstance(value, (list, tuple)):
        value = "\n".join(str(v) for v in value if str(v).strip())
    return str(value).strip() or NA


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{n}. {x}" for n, x in enumerate(items, 1)) if items else NA


class _Refs:
    def __init__(self, chunk_cites: dict[str, str]):
        self.cites = chunk_cites

    def __call__(self, sources: list[str]) -> str:
        return "\n".join(f"{self.cites.get(s, s)} [{s}]" for s in sources) or NA


def _paragraphs(text: str) -> list[Block]:
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    return [Block("para", p) for p in parts] or [Block("para", NA)]


def _list_or_na(items: list[str], kind: str = "bullets") -> Block:
    items = [i for i in items if str(i).strip()]
    return Block(kind, items=items) if items else Block("para", NA)


def _covered(names: list[str], found: list[str]) -> list[str]:
    """Template categories with no matching entry in the sources."""
    found_l = [f.lower() for f in found]
    return [n for n in names if not any(n.lower().split()[0] in f or f in n.lower() for f in found_l)]


def _status(req_id: str, sources: list[str], questions: list[OpenQuestion]) -> str:
    if not sources:
        return "No source: to be confirmed"
    if any(req_id in q.related_requirements for q in questions):
        return "Pending clarification"
    return "Confirmed"


def _priority(item) -> str:
    return f"{item.priority}\n({item.priority_basis})" if item.priority_basis and item.priority != "To be confirmed" \
        else item.priority


def build_blocks(frd: FRD, chunk_cites: dict[str, str]) -> list[Block]:
    ctx, spec, info, refs = frd.context, frd.specification, frd.info, _Refs(chunk_cites)
    reqs, rbar, use_cases, questions = frd.requirements, frd.access.access_requirements, frd.use_cases, frd.open_questions
    signoff = [p for p in ctx.people if p.signoff_authority]
    team = [p for p in ctx.people if not p.signoff_authority]
    uc_by_req: dict[str, list[str]] = {}
    for uc in use_cases:
        for rid in uc.requirements:
            uc_by_req.setdefault(rid, []).append(f"{uc.id} {uc.name}")
    b: list[Block] = [Block("heading", info.title, 0)]

    b.append(Block("heading", "1. Document Information", 1))
    b.append(Block("table", headers=["Field", "Detail"], items=[
        ["Document Title", info.title], ["Prepared By", _or_na(info.prepared_by)],
        ["Date", info.date], ["Version", info.version]]))

    b.append(Block("heading", "2. Stakeholder – Document Signoff Authority", 1))
    b.append(Block("table", headers=["Stakeholder Name", "Title", "Sign-off Date", "Contact Information"], items=[
        [p.name, _or_na(p.title), "Pending", _or_na([p.email, p.phone])] for p in signoff]
        or [[NA, NA, "Pending", NA]]))

    b.append(Block("heading", "3. Team Contact Information", 1))
    b.append(Block("table", headers=["Name", "Title", "Email"],
                   items=[[p.name, _or_na(p.title), _or_na(p.email)] for p in team] or [[NA, NA, NA]]))

    b.append(Block("heading", "4. Revision History", 1))
    b.append(Block("table", headers=["Version", "Date", "Author", "Description of Change"], items=[
        [info.version, info.date, _or_na(info.prepared_by),
         f"Initial draft generated from {len(frd.sources)} source document(s)."]]))

    b.append(Block("heading", "5. Objective", 1))
    b += _paragraphs(frd.summary.objective)

    b.append(Block("heading", "6. Overview", 1))
    b += _paragraphs(frd.summary.overview)
    b.append(Block("heading", "6.1 Purpose of the Initiative", 2))
    b += _paragraphs(frd.summary.initiative_purpose or ctx.purpose)
    b.append(Block("heading", "6.2 In-Scope", 2))
    b.append(_list_or_na(ctx.scope_in))
    b.append(Block("heading", "6.3 Out-of-Scope", 2))
    b.append(_list_or_na(ctx.scope_out))

    b.append(Block("heading", "7. End User Roles", 1))
    b.append(Block("table", headers=["Role", "Description", "Responsibilities", "Access / Restrictions", "Source Reference"],
                   items=[[r.role, _or_na(r.description), _or_na(r.responsibilities), _or_na(r.access), refs(r.sources)]
                          for r in ctx.roles] or [[NA, NA, NA, NA, NA]]))

    b.append(Block("heading", "8. Functional Requirements Summary", 1))
    b.append(Block("table", headers=["Requirement ID", "Requirement Description", "Priority", "Module",
                                     "Acceptance Criteria", "Source Reference"],
                   items=[[r.id, r.description, _priority(r), r.module, _numbered(r.acceptance_criteria), refs(r.sources)]
                          for r in reqs] or [[NA, NA, NA, NA, NA, NA]]))

    b.append(Block("heading", "9. Role-Based Access Summary", 1))
    if rbar:
        b.append(Block("table", headers=["Requirement ID", "Authorization Requirement", "Priority", "Module",
                                         "Acceptance Criteria", "Source Reference"],
                       items=[[a.id, a.requirement, _priority(a), _or_na(a.module), _numbered(a.acceptance_criteria),
                               refs(a.sources)] for a in rbar]))
    else:
        b.append(Block("para", f"{NA}: no role-based access requirements could be derived from the sources."))

    b.append(Block("heading", "10. Detailed Functional Requirements", 1))
    if not use_cases:
        b.append(Block("para", NA))
    for i, uc in enumerate(use_cases, 1):
        b.append(Block("heading", f"10.{i} Use Case: {uc.name} ({uc.id})", 2))
        b.append(Block("label", "User Story:"))
        b.append(Block("para", _or_na(uc.user_story)))
        b.append(Block("label", "User Roles:"))
        b.append(Block("para", ", ".join(uc.roles) or NA))
        b.append(Block("label", "Preconditions:"))
        b.append(_list_or_na(uc.preconditions))
        b.append(Block("label", "Actions Performed:"))
        b.append(_list_or_na(uc.actions, "numbered"))
        b.append(Block("label", "Business Rules:"))
        b.append(_list_or_na(uc.business_rules))
        b.append(Block("label", "Expected Result:"))
        b.append(Block("para", _or_na(uc.expected_result)))
        b.append(Block("label", "Exception / Error Handling:"))
        b.append(_list_or_na(uc.exceptions))
        b.append(Block("label", "Source Traceability:"))
        b.append(Block("para", f"Requirements: {', '.join(uc.requirements)}. Sources: "
                               + (refs(uc.sources).replace(chr(10), '; ') if uc.sources else NA) + "."))

    b.append(Block("heading", "11. Role-Based Access Matrix", 1))
    b.append(Block("table", headers=["Role", "Access Scope", "Allowed Actions", "Expected Restrictions", "Source Reference"],
                   items=[[m.role, _or_na(m.scope), _or_na(m.allowed_actions), _or_na(m.restrictions), refs(m.sources)]
                          for m in frd.access.matrix] or [[NA, NA, NA, NA, NA]]))

    b.append(Block("heading", "12. Assumptions and Dependencies", 1))
    b.append(Block("heading", "12.1 Assumptions", 2))
    b.append(Block("table", headers=["ID", "Assumption", "Source Reference"],
                   items=[[a.id, a.assumption, refs(a.sources)] for a in spec.assumptions] or [[NA, NA, NA]]))
    b.append(Block("heading", "12.2 Dependencies", 2))
    b.append(Block("table", headers=["ID", "Dependency", "Description", "Source Reference"],
                   items=[[d.id, d.dependency, _or_na(d.description), refs(d.sources)] for d in spec.dependencies]
                   or [[NA, NA, NA, NA]]))

    b.append(Block("heading", "13. Status Definitions", 1))
    if spec.statuses:
        b.append(Block("table", headers=["Status", "Description", "Applicable Transition or Condition", "Source Reference"],
                       items=[[s.status, _or_na(s.description), _or_na(s.transition), refs(s.sources)]
                              for s in spec.statuses]))
    else:
        b.append(Block("para", f"{NA}: the sources define no workflow statuses."))

    b.append(Block("heading", "14. Priority Definitions", 1))
    b.append(Block("table", headers=["Priority", "Definition", "Functional", "Access"], items=[
        [p, PRIORITY_DEFINITIONS[p], str(sum(r.priority == p for r in reqs)), str(sum(a.priority == p for a in rbar))]
        for p in PRIORITIES]))
    b.append(Block("para", "Priorities are assigned only where the source material states or clearly implies them; "
                           "the source wording is shown beneath each priority. Source terms are mapped as follows: "
                           "critical, blocker or legally required to Critical; must have, mandatory or P1 to High; "
                           "should have or P2 to Medium; could have, nice to have or P3 to Low."))

    b.append(Block("heading", "15. Non-Functional Requirements", 1))
    if spec.non_functional:
        b.append(Block("table", headers=["ID", "Category", "Requirement", "Source Reference"],
                       items=[[n.id, n.category, n.requirement, refs(n.sources)] for n in spec.non_functional]))
    missing = _covered(NFR_CATEGORIES, [n.category for n in spec.non_functional])
    if missing:
        b.append(Block("para", f"{NA} for: {', '.join(missing)}."))

    b.append(Block("heading", "16. Integration Requirements", 1))
    if not spec.integrations:
        b.append(Block("para", f"{NA}: the sources describe no system integrations."))
    for i, g in enumerate(spec.integrations, 1):
        b.append(Block("heading", f"16.{i} {g.system}", 2))
        b.append(Block("table", headers=["Attribute", "Detail"], items=[
            ["Integrated System", g.system], ["Purpose", _or_na(g.purpose)], ["Data Exchanged", _or_na(g.data_exchanged)],
            ["Integration Direction", _or_na(g.direction)], ["Authentication / Authorization", _or_na(g.authentication)],
            ["Trigger or Frequency", _or_na(g.trigger)], ["Error Handling", _or_na(g.error_handling)],
            ["Dependency", _or_na(g.dependency)], ["Source Reference", refs(g.sources)]]))

    b.append(Block("heading", "17. UI and Interaction Guidelines", 1))
    if spec.ui_standards:
        b.append(Block("para", f"Existing UI standards: {spec.ui_standards}"))
    if spec.ui_guidelines:
        b.append(Block("table", headers=["Area", "Guideline", "Source Reference"],
                       items=[[u.area, u.guideline, refs(u.sources)] for u in spec.ui_guidelines]))
    missing = _covered(UI_AREAS, [u.area for u in spec.ui_guidelines])
    if missing:
        b.append(Block("para", f"{NA} for: {', '.join(missing)}."))

    b.append(Block("heading", "18. Open Questions and Clarifications", 1))
    b.append(Block("table", headers=["ID", "Question / Clarification Required", "Related Requirement", "Source",
                                     "Status", "Owner"],
                   items=[[q.id, q.question, ", ".join(q.related_requirements) or "General", refs(q.sources), q.status,
                           _or_na(q.owner)] for q in questions] or [["None", "No open questions were raised.", "", "", "", ""]]))

    b.append(Block("heading", "19. Requirements Traceability Matrix", 1))
    b.append(Block("table", headers=["Requirement ID", "Requirement", "Source Document / Source Reference",
                                     "Related Use Case", "Acceptance Criteria", "Status"],
                   items=[[r.id, r.title, refs(r.sources), "\n".join(uc_by_req.get(r.id, [])) or "None",
                           f"{len(r.acceptance_criteria)} criteria (section 20)" if r.acceptance_criteria else "To be defined",
                           _status(r.id, r.sources, questions)] for r in reqs]
                   + [[a.id, a.requirement, refs(a.sources), "None",
                       f"{len(a.acceptance_criteria)} criteria (section 20)" if a.acceptance_criteria else "To be defined",
                       _status(a.id, a.sources, questions)] for a in rbar]))

    b.append(Block("heading", "20. Acceptance Criteria", 1))
    b.append(Block("para", "Each criterion describes observable system behaviour that testers and stakeholders can verify."))
    b.append(Block("table", headers=["Requirement ID", "Requirement", "Acceptance Criteria"],
                   items=[[r.id, r.title, _numbered(r.acceptance_criteria)] for r in reqs]
                   + [[a.id, a.requirement, _numbered(a.acceptance_criteria)] for a in rbar]))

    b.append(Block("heading", "21. Document Signoff", 1))
    b.append(Block("table", headers=["Stakeholder Name", "Role / Title", "Approval Status", "Sign-off Date", "Comments"],
                   items=[[p.name, _or_na(p.title), "Pending", "", ""] for p in signoff] or [[NA, NA, "Pending", "", ""]]))

    b.append(Block("heading", "Appendix A. Source Documents", 1))
    processed = [f for f in frd.files if f.status == "processed"]
    b.append(Block("table", headers=["ID", "Document", "Type", "Passages"],
                   items=[[f.doc_id, f.path, f.type, str(f.passages)] for f in processed]
                   or [[k, v, "", ""] for k, v in frd.sources.items()]))
    excluded = [f for f in frd.files if f.status in ("skipped", "duplicate", "failed")]
    if excluded:
        b.append(Block("heading", "Appendix B. Files Not Included", 1))
        b.append(Block("para", "These input files were not used to build this document."))
        b.append(Block("table", headers=["File", "Status", "Reason"],
                       items=[[f.path, f.status.capitalize(), f.reason] for f in excluded]))
    return b


# --- Markdown ----------------------------------------------------------------------------------

def _md_cell(text: str) -> str:
    return html.escape(str(text), quote=False).replace("|", "\\|").replace("\n", "<br>")


def to_markdown(blocks: list[Block]) -> str:
    out: list[str] = []
    for blk in blocks:
        if blk.kind == "heading":
            out.append(f"{'#' * (blk.level + 1)} {blk.text}")
        elif blk.kind == "para":
            out.append(blk.text)
        elif blk.kind == "label":
            out.append(f"**{blk.text}**")
        elif blk.kind == "note":
            out.append(f"> {blk.text}")
        elif blk.kind == "bullets":
            out.append("\n".join(f"- {x}" for x in blk.items))
        elif blk.kind == "numbered":
            out.append("\n".join(f"{n}. {x}" for n, x in enumerate(blk.items, 1)))
        elif blk.kind == "table":
            rows = [blk.headers, ["---"] * len(blk.headers), *blk.items]
            out.append("\n".join("| " + " | ".join(c if c == "---" else _md_cell(c) for c in row) + " |" for row in rows))
    return "\n\n".join(out) + "\n"


# --- Word --------------------------------------------------------------------------------------

def to_docx(blocks: list[Block], path: Path) -> None:
    doc = Document()
    doc.styles["Normal"].font.name = "Calibri"
    doc.styles["Normal"].font.size = Pt(10.5)
    for blk in blocks:
        if blk.kind == "heading":
            doc.add_heading(blk.text, level=blk.level)
        elif blk.kind == "para":
            doc.add_paragraph(blk.text)
        elif blk.kind == "label":
            doc.add_paragraph().add_run(blk.text).bold = True
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


# --- HTML preview ------------------------------------------------------------------------------

def to_html(blocks: list[Block]) -> str:
    """A fragment for the web preview. Every piece of text is escaped; sections get ids for navigation."""
    e = lambda t: html.escape(str(t)).replace("\n", "<br>")  # noqa: E731
    out: list[str] = []
    section = 0
    open_section = False
    for blk in blocks:
        if blk.kind == "heading" and blk.level == 0:
            continue
        if blk.kind == "heading" and blk.level == 1:
            if open_section:
                out.append("</section>")
            section += 1
            out.append(f'<section id="sec-{section}"><h2>{e(blk.text)}</h2>')
            open_section = True
        elif blk.kind == "heading":
            out.append(f"<h{blk.level + 1}>{e(blk.text)}</h{blk.level + 1}>")
        elif blk.kind == "para":
            cls = ' class="na"' if blk.text.startswith(NA) else ""
            out.append(f"<p{cls}>{e(blk.text)}</p>")
        elif blk.kind == "label":
            out.append(f'<p class="label">{e(blk.text)}</p>')
        elif blk.kind == "note":
            out.append(f'<p class="note">{e(blk.text)}</p>')
        elif blk.kind in ("bullets", "numbered"):
            tag = "ul" if blk.kind == "bullets" else "ol"
            out.append(f"<{tag}>" + "".join(f"<li>{e(x)}</li>" for x in blk.items) + f"</{tag}>")
        elif blk.kind == "table":
            head = "".join(f"<th>{e(h)}</th>" for h in blk.headers)
            rows = "".join("<tr>" + "".join(f"<td>{e(c)}</td>" for c in row) + "</tr>" for row in blk.items)
            out.append(f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table></div>')
    if open_section:
        out.append("</section>")
    return "\n".join(out)
