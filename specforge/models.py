from typing import Literal

from pydantic import BaseModel, Field, field_validator

Priority = Literal["Must", "Should", "Could"]
_PRIORITY_ALIASES = {"high": "Must", "critical": "Must", "must": "Must", "must have": "Must",
                     "medium": "Should", "should": "Should", "should have": "Should",
                     "low": "Could", "could": "Could", "could have": "Could", "nice to have": "Could"}


class Stakeholder(BaseModel):
    role: str
    description: str = ""


class Module(BaseModel):
    name: str
    description: str = ""
    search_queries: list[str] = Field(default_factory=list)


class ProjectContext(BaseModel):
    title: str
    purpose: str
    scope_in: list[str] = Field(default_factory=list)
    scope_out: list[str] = Field(default_factory=list)
    stakeholders: list[Stakeholder] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    modules: list[Module] = Field(min_length=1)


class Requirement(BaseModel):
    id: str = ""
    module: str = ""
    title: str
    description: str
    actor: str = ""
    priority: Priority = "Should"
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    sources: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @field_validator("priority", mode="before")
    @classmethod
    def _normalise_priority(cls, v):
        return _PRIORITY_ALIASES.get(str(v).strip().lower(), "Should")

    @field_validator("notes", mode="before")
    @classmethod
    def _notes_as_list(cls, v):
        if v is None or v == "":
            return []
        return [v] if isinstance(v, str) else v


class RequirementList(BaseModel):
    requirements: list[Requirement] = Field(default_factory=list)


class Merge(BaseModel):
    keep: str
    drop: list[str]
    reason: str = ""

    @field_validator("drop", mode="before")
    @classmethod
    def _drop_as_list(cls, v):
        return [v] if isinstance(v, str) else v


class Issue(BaseModel):
    req_id: str
    type: Literal["ambiguous", "conflict", "incomplete", "untestable", "other"] = "other"
    note: str

    @field_validator("type", mode="before")
    @classmethod
    def _normalise_type(cls, v):
        v = str(v).strip().lower()
        return v if v in {"ambiguous", "conflict", "incomplete", "untestable"} else "other"


class Review(BaseModel):
    merges: list[Merge] = Field(default_factory=list)
    issues: list[Issue] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class Summary(BaseModel):
    executive_summary: str
    module_overviews: dict[str, str] = Field(default_factory=dict)


class SourceFile(BaseModel):
    """One input file and what happened to it."""
    path: str  # relative path shown to users, e.g. "specs/billing/rules.pdf"
    type: str
    size: int
    status: Literal["pending", "processed", "skipped", "duplicate", "failed"] = "pending"
    reason: str = ""
    doc_id: str | None = None
    passages: int = 0


class FRD(BaseModel):
    context: ProjectContext
    summary: Summary
    requirements: list[Requirement]
    open_questions: list[str]
    sources: dict[str, str]
    files: list[SourceFile] = Field(default_factory=list)
