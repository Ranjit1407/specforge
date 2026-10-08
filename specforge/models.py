from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field, field_validator

NOT_SPECIFIED = "Not specified in source material"

Priority = Literal["Critical", "High", "Medium", "Low", "To be confirmed"]
PRIORITIES: tuple[str, ...] = Priority.__args__
_PRIORITY_ALIASES = {
    "critical": "Critical", "blocker": "Critical", "showstopper": "Critical",
    "high": "High", "must": "High", "must have": "High", "mandatory": "High", "p1": "High",
    "medium": "Medium", "should": "Medium", "should have": "Medium", "p2": "Medium",
    "low": "Low", "could": "Low", "could have": "Low", "nice to have": "Low", "p3": "Low",
}


def _to_text(v) -> str:
    """Models sometimes answer a text field with a list or an object; keep the content as readable text."""
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, dict):
        return "; ".join(f"{k}: {_to_text(x)}" for k, x in v.items() if _to_text(x).strip())
    if isinstance(v, (list, tuple)):
        return "; ".join(t for t in (_to_text(x) for x in v) if t.strip())
    return str(v)


def _to_text_list(v) -> list[str]:
    if v is None or v == "":
        return []
    items = v if isinstance(v, (list, tuple)) else [v]
    return [t for t in (_to_text(x) for x in items) if t.strip()]


Text = Annotated[str, BeforeValidator(_to_text)]
TextList = Annotated[list[str], BeforeValidator(_to_text_list)]


class Sourced(BaseModel):
    """Anything that cites source passages by excerpt ID, e.g. ["D1-004"]."""
    sources: TextList = Field(default_factory=list)



class Prioritised(Sourced):
    priority: Priority = "To be confirmed"
    priority_basis: Text = ""  # the source wording the priority was taken from

    @field_validator("priority", mode="before")
    @classmethod
    def _normalise_priority(cls, v):
        text = str(v or "").strip().lower()
        return _PRIORITY_ALIASES.get(text, "To be confirmed")


class Module(BaseModel):
    name: Text
    description: Text = ""
    search_queries: TextList = Field(default_factory=list)


class UserRole(Sourced):
    role: Text
    description: Text = ""
    responsibilities: TextList = Field(default_factory=list)
    access: Text = ""  # access level or restrictions stated in the sources


class Person(Sourced):
    name: Text
    title: Text = ""
    email: Text = ""
    phone: Text = ""
    signoff_authority: bool = False


class OpenQuestion(Sourced):
    id: Text = ""
    question: Text
    related_requirements: TextList = Field(default_factory=list)
    owner: Text = ""
    status: Text = "Open"



class ProjectContext(BaseModel):
    title: Text
    purpose: Text
    scope_in: TextList = Field(default_factory=list)
    scope_out: TextList = Field(default_factory=list)
    roles: list[UserRole] = Field(default_factory=list)
    people: list[Person] = Field(default_factory=list)
    modules: list[Module] = Field(min_length=1)
    open_questions: list[OpenQuestion] = Field(default_factory=list)


class Assumption(Sourced):
    id: Text = ""
    assumption: Text


class Dependency(Sourced):
    id: Text = ""
    dependency: Text
    description: Text = ""


class StatusDefinition(Sourced):
    status: Text
    description: Text = ""
    transition: Text = ""


class NonFunctionalRequirement(Sourced):
    id: Text = ""
    category: Text
    requirement: Text


class Integration(Sourced):
    system: Text
    purpose: Text = ""
    data_exchanged: Text = ""
    direction: Text = ""
    authentication: Text = ""
    trigger: Text = ""
    error_handling: Text = ""
    dependency: Text = ""


class UIGuideline(Sourced):
    area: Text
    guideline: Text


class Specification(BaseModel):
    """Supporting detail around the functional requirements, gathered from the whole material."""
    assumptions: list[Assumption] = Field(default_factory=list)
    dependencies: list[Dependency] = Field(default_factory=list)
    statuses: list[StatusDefinition] = Field(default_factory=list)
    non_functional: list[NonFunctionalRequirement] = Field(default_factory=list)
    integrations: list[Integration] = Field(default_factory=list)
    ui_guidelines: list[UIGuideline] = Field(default_factory=list)
    ui_standards: Text = ""  # set when the sources say to follow an existing application's UI standards
    open_questions: list[OpenQuestion] = Field(default_factory=list)


class Requirement(Prioritised):
    id: Text = ""
    module: Text = ""
    title: Text
    description: Text
    actor: Text = ""
    inputs: TextList = Field(default_factory=list)
    outputs: TextList = Field(default_factory=list)
    business_rules: TextList = Field(default_factory=list)
    acceptance_criteria: TextList = Field(default_factory=list)
    notes: TextList = Field(default_factory=list)



class RequirementList(BaseModel):
    requirements: list[Requirement] = Field(default_factory=list)


class AccessRequirement(Prioritised):
    id: Text = ""
    requirement: Text
    module: Text = ""
    acceptance_criteria: TextList = Field(default_factory=list)


class AccessEntry(Sourced):
    role: Text
    scope: Text = ""
    allowed_actions: TextList = Field(default_factory=list)
    restrictions: TextList = Field(default_factory=list)


class AccessModel(BaseModel):
    access_requirements: list[AccessRequirement] = Field(default_factory=list)
    matrix: list[AccessEntry] = Field(default_factory=list)
    open_questions: list[OpenQuestion] = Field(default_factory=list)


class UseCase(Sourced):
    id: Text = ""
    name: Text
    module: Text = ""
    user_story: Text = ""
    roles: TextList = Field(default_factory=list)
    preconditions: TextList = Field(default_factory=list)
    actions: TextList = Field(default_factory=list)
    business_rules: TextList = Field(default_factory=list)
    expected_result: Text = ""
    exceptions: TextList = Field(default_factory=list)
    requirements: TextList = Field(default_factory=list)


class UseCaseList(BaseModel):
    use_cases: list[UseCase] = Field(default_factory=list)


class Merge(BaseModel):
    keep: Text
    drop: TextList
    reason: Text = ""



class Issue(BaseModel):
    req_id: Text
    type: Literal["ambiguous", "conflict", "incomplete", "untestable", "other"] = "other"
    note: Text

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
    objective: Text
    overview: Text
    initiative_purpose: Text = ""


class DocumentInfo(BaseModel):
    title: Text
    prepared_by: Text = ""
    date: Text
    version: Text = "1.0 (Draft)"


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
