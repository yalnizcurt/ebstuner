"""Typed public response contracts; retain compiler/inspection extension metadata."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class View(BaseModel):
    model_config = ConfigDict(extra='allow')


class ProjectIdentity(View):
    id: str
    name: str
    version: int = Field(ge=1)
    data_version: int | None = None


class BranchMatch(View):
    index: int
    table: str


class FieldView(View):
    name: str
    branch: int
    table: str
    start: int
    end: int
    width: int
    dtype: str
    raw: str
    original: str
    value: str | None
    modified: bool
    readonly: bool
    missing_bytes: int


class RecordView(View):
    record: int
    length: int
    record_type: str
    branches: list[BranchMatch]
    uncertain_branches: list[int]
    unmatched_branches: list[int]
    minimum_length: int
    issues: list[str]
    findings: list[dict]
    classification: Literal['valid','formatting_discrepancy','recoverable_structural_error','semantic_inconsistency','ambiguous_corruption','unsupported_operation']
    status: Literal['valid','needs_review']


class RecordDetail(RecordView):
    fields: list[FieldView]
    raw: str
    original_raw: str
    hex: str
    original_hex: str
    line_ending: Literal['CRLF','CR','LF','none']
    checksum: str


class DiagnosisView(View):
    sha256: str
    branch_count: int
    record_count: int
    counts: dict[str,int]
    records_with_findings: int
    encoding: str
    record_format: str
    compatibility: str
    export_status: Literal['structurally_validated','validated_with_warnings','blocked_by_errors','not_fully_verifiable']
    validation_scope: str
    records: list[RecordView] = Field(default_factory=list)


class ScoreFeature(View):
    weight: float
    feature: float
    points: float


class ProposalView(View):
    id: str
    record: int
    kind: str
    source: Literal['DETERMINISTIC','SPREADSHEET','BEDROCK','GROQ']
    status: Literal['PROPOSED','PENDING_REVIEW','APPROVED','REJECTED','APPLIED','SUPERSEDED']
    record_checksum: str
    candidate_score: float
    score_breakdown: dict[str,ScoreFeature]
    reason: str
    requires_approval: bool
    evidence: list[dict]


class ProjectState(View):
    project: ProjectIdentity
    loaded: bool
    diagnosis: DiagnosisView | None = None
    record: RecordDetail | None = None
    proposals: list[ProposalView] = Field(default_factory=list)


class RecordPage(View):
    version: int
    total: int
    records: list[RecordDetail]


class ProposalPage(View):
    total: int
    proposals: list[ProposalView]
