from dataclasses import dataclass, field
from pathlib import Path

SCHEMA_VERSION = "t2-facts-v1"
RULE_VERSION = "t2-html-proof-v1"
LOCATOR_VERSION = "t2-html-locator-v1"
SERIALIZATION_VERSION = "t2-canonical-json-v1"
STATES = {"verified", "unresolved", "conflict", "rejected"}
IDENTITIES = {"resolved", "identity_unresolved", "not_applicable"}
ELIGIBILITY = {"eligible", "identity_unresolved", "scope_unresolved", "overlap_unresolved", "not_applicable"}
FIELD_ENUMS = {
    ('bid_subject','subject_kind'):{'individual','consortium'},
    ('event','scope_status'):{'resolved','scope_unresolved'},
    ('roster','roster_kind'):{'all_bidders','qualified_only','topn_candidates','unknown'},
    ('roster','disclosure_status'):{'disclosed_complete','disclosed_partial','not_disclosed','parse_failed'},
    ('participation','participation_kind'):{'award_only','procurement_participation','bid_participation'},
    ('participation','result'):{'won','lost','unknown'},
    ('supply','provider_kind'):{'brand','product_supplier','unknown'},
    ('amount','amount_kind'):{'award_total','project_total','product_line_total','budget','ceiling','agency_fee','unit_price','rate','unknown'},
    ('amount','coverage_status'):{'isolated','explicit_parent','explicit_child','unknown_overlap','not_applicable'},
}
for _kind in ('roster','participation','award','supply','amount'):
    FIELD_ENUMS[(_kind,'scope_level')]={'project','event','unresolved'}
FIELDS = {
    "organization": "raw_name exact_key identity_basis alias_evidence_ids",
    "bid_subject": "subject_kind member_ids leader_ids",
    "project": "raw_name project_code round_raw buyer_organization_id",
    "event": "project_id lot_key lot_raw round_raw scope_status",
    "roster": "scope_id scope_level roster_kind declared_names validated_subject_ids disclosure_status completeness_evidence_ids",
    "participation": "scope_id scope_level subject_id participation_kind result qualification_raw score_raw roster_id",
    "award": "scope_id scope_level subject_id amount_ids",
    "supply": "scope_id scope_level product_candidate_id provider_kind provider_organization_id brand_raw",
    "amount": "scope_id scope_level owner_subject_id product_candidate_id amount_kind raw_value candidate_values decimal_value currency original_unit coverage_status covers_amount_ids overlaps_amount_ids",
}

class InputContractError(ValueError): pass
class InputChangedError(ValueError): pass
class BoundaryViolation(RuntimeError): pass
class ArtifactConflictError(ValueError): pass
class BundleValidationError(ValueError): pass

@dataclass
class Source:
    dataset_id: str
    source_id: str
    announcement_id: str
    html_sha256: str | None
    encoding: str | None
    read_status: str

@dataclass
class Candidate:
    dataset_id: str
    candidate_id: str
    announcement_id: str
    file_sha256: str
    json_path: str
    field_kind: str
    raw_value: object
    producer: str = "legacy_model_unknown"

@dataclass
class Locator:
    start: int
    end: int
    section: str = ""
    table: int | None = None
    row: int | None = None
    cell: int | None = None
    parent_physical_cell: tuple | None = None
    version: str = LOCATOR_VERSION

@dataclass
class Evidence:
    dataset_id: str
    evidence_id: str
    source_id: str
    locator: Locator
    raw_quote: str
    header_context: str
    candidate_ids: list[str] = field(default_factory=list)
    fact_ids: list[str] = field(default_factory=list)

@dataclass
class Fact:
    dataset_id: str
    record_id: str
    kind: str
    natural_key: tuple[str, ...]
    fields: dict
    validation_status: str = "unresolved"
    identity_status: str = "not_applicable"
    aggregation_eligibility: str = "not_applicable"
    evidence_ids: list[str] = field(default_factory=list)
    candidate_ids: list[str] = field(default_factory=list)

    def __post_init__(self):
        if self.kind not in FIELDS or set(self.fields) != set(FIELDS[self.kind].split()):
            raise ValueError("invalid fact kind or fields")
        if self.validation_status not in STATES or self.identity_status not in IDENTITIES or self.aggregation_eligibility not in ELIGIBILITY:
            raise ValueError("invalid fact state")
        for (kind,key),values in FIELD_ENUMS.items():
            if kind == self.kind and self.fields[key] not in values:
                raise ValueError('invalid field enum: '+kind+'.'+key)
        if self.aggregation_eligibility == "eligible" and (self.identity_status == "identity_unresolved" or self.validation_status != "verified"):
            raise ValueError("unresolved fact cannot be aggregate eligible")

@dataclass
class Issue:
    dataset_id: str
    issue_id: str
    announcement_id: str
    reason_code: str
    candidate_ids: list[str] = field(default_factory=list)
    fact_ids: list[str] = field(default_factory=list)
    locator: Locator | None = None
    details: dict = field(default_factory=dict)

@dataclass
class InputSnapshot:
    input_manifest: dict
    legacy_rows: list[dict]
    v11_rows: list[dict]
    html_entries: dict[str, Path | None]
    dataset_id: str
    legacy_path: Path
    v11_path: Path
    html_root: Path
    implementation: dict

@dataclass
class Section:
    start: int
    end: int
    text: str
    title: str = ""
    positions: list[tuple[int, int]] = field(default_factory=list)

@dataclass
class PhysicalCell(Section):
    table: int = 0
    row: int = 0
    cell: int = 0
    rowspan: int = 1
    colspan: int = 1
    is_header: bool = False

@dataclass
class CellRef:
    parent_physical_cell: tuple[int, int, int]

@dataclass
class TableIndex:
    table: int
    start: int
    end: int
    rows: list[list[PhysicalCell]]
    heading: str = ""
    malformed: bool = False

@dataclass
class SourceIndex:
    source_id: str
    announcement_id: str
    html_sha256: str
    encoding: str | None
    decoded_html: str
    sections: list[Section]
    tables: list[TableIndex]
    diagnostics: list[Issue]
    dataset_id: str = ""

@dataclass
class Bundle:
    dataset_id: str
    sources: list[Source] = field(default_factory=list)
    candidates: list[Candidate] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

@dataclass
class ValidationReport:
    valid: bool
    errors: list[str]
    coverage: dict

@dataclass
class BuildResult:
    dataset_id: str
    status: str
    output_dir: str
    manifest: dict
