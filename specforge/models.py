from typing import Literal

from pydantic import BaseModel, Field, field_validator

NOT_SPECIFIED = "Not specified in source material"

Priority = Literal["Critical", "High", "Medium", "Low", "To be confirmed"]
PRIORITIES: tuple[str, ...] = Priority.__args__
_PRIORITY_ALIASES = {
    "critical": "Critical", "blocker": "Critical", "showstopper": "Critical",
    "high": "High", "must": "High", "must have": "High", "mandatory": "High", "p1": "High",
    "medium": "Medium", "should": "Medium", "should have": "Medium", "p2": "Medium",
    "low": "Low", "could": "Low", "could have": "Low", "nice to have": "Low", "p3": "Low",
}


def _as_list(v):
    if v is None or v == "":
        return []
    return [v] if isinstance(v, str) else v


class Sourced(BaseModel):
    """Anything that cites source passages by excerpt ID, e.g. ["D1-004"]."""
    sources: list[str] = Field(default_factory=list)

    @field_validator("sources", mode="before")
    @classmethod
    def _sources_as_list(cls, v):
        return _as_list(v)


class Prioritised(Sourced):
    priority: Priority = "To be confirmed"
    priority_basis: str = ""  # the source wording the priority was taken from

    @field_validator("priority", mode="before")
    @classmethod
    def _normalise_priority(cls, v):
        text = str(v or "").strip().lower()
        return _PRIORITY_ALIASES.get(text, "To be confirmed")


class Module(BaseModel):
    name: str
    description: str = ""
    search_queries: list[str] = Field(default_factory=list)


class UserRole(Sourced):
    role: str
    description: str = ""
    responsibilities: list[str] = Field(default_factory=list)
    access: str = ""  # access level or restrictions stated in the sources


class Person(Sourced):
    name: str
    title: str = ""
    email: str = ""
    phone: str = ""
    signoff_authority: bool = False


class OpenQuestion(Sourced):
    id: str = ""
    question: str
    related_requirements: list[str] = Field(default_factory=list)
    owner: str = ""
    status: str = "Open"

    @field_validator("related_requirements", mode="before")
    @classmethod
    def _related_as_list(cls, v):
        return _as_list(v)


class ProjectContext(BaseModel):
    title: str
    purpose: str
    scope_in: list[str] = Field(default_factory=list)
    scope_out: list[str] = Field(default_factory=list)
    roles: list[UserRole] = Field(default_factory=list)
    people: list[Person] = Field(default_factory=list)
    modules: list[Module] = Field(min_length=1)
    open_questions: list[OpenQuestion] = Field(default_factory=list)


class Assumption(Sourced):
    id: str = ""
    assumption: str


class Dependency(Sourced):
    id: str = ""
    dependency: str
    description: str = ""


class StatusDefinition(Sourced):
    status: str
    description: str = ""
    transition: str = ""


class NonFunctionalRequirement(Sourced):
    id: str = ""
    category: str
    requirement: str


class Integration(Sourced):
    system: str
    purpose: str = ""
    data_exchanged: str = ""
    direction: str = ""
    authentication: str = ""
    trigger: str = ""
    error_handling: str = ""
    dependency: str = ""


class UIGuideline(Sourced):
    area: str
    guideline: str


class Specification(BaseModel):
    """Supporting detail around the functional requirements, gathered from the whole material."""
    assumptions: list[Assumption] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    statuses: list[StatusDefinition] = Field(default_factory=list)
    non_functional: list[NonFunctionalRequirement] = Field(default_factory=list)
    integrations: list[Integration] = Field(default_factory=list)
    ui_guidelines: list[UIGuideline] = Field(default_factory=list)
    ui_standards: str = ""  # set when the sources say to follow an existing application's UI standards
    open_questions: list[OpenQuestion] = Field(default_factory=list)


class Requirement(Prioritised):
    id: str = ""
    module: str = ""
    title: str
    description: str
    actor: str = ""
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @field_validator("notes", mode="before")
    @classmethod
    def _notes_as_list(cls, v):
        return _as_list(v)


class RequirementList(BaseModel):
    requirements: list[Requirement] = Field(default_factory=list)


class AccessRequirement(Prioritised):
    id: str = ""
    requirement: str
    module: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)


class AccessEntry(Sourced):
    role: str
    scope: str = ""
    allowed_actions: list[str] = Field(default_factory=list)
    restrictions: list[str] = Field(default_factory=list)


class AccessModel(BaseModel):
    access_requirements: list[AccessRequirement] = Field(default_factory=list)
    matrix: list[AccessEntry] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)


class UseCase(Sourced):
    id: str = ""
    name: str
    module: str = ""
    user_story: str = ""
    roles: list[str] = Field(default_factory=list)
    preconditions: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    business_rules: list[str] = Field(default_factory=list)
    expected_result: str = ""
    exceptions: list[str] = Field(default_factory=list)
    requirements: list[str] = Field(default_factory=list)


class UseCaseList(BaseModel):
    use_cases: list[UseCase] = Field(default_factory=list)


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
    open_questions: list[OpenQuestion] = Field(default_factory=list)


class Summary(BaseModel):
    objective: str
    overview: str
    initiative_purpose: str = ""


class DocumentInfo(BaseModel):
    title: str
    prepared_by: str = ""
    date: str
    version: str = "1.0 (Draft)"


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
    info: DocumentInfo
    context: ProjectContext
    summary: Summary
    specification: Specification
    requirements: list[Requirement]
    access: AccessModel
    use_cases: list[UseCase]
    open_questions: list[OpenQuestion]
    sources: dict[str, str]
    files: list[SourceFile] = Field(default_factory=list)
