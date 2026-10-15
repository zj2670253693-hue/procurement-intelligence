"""Adapt existing algorithms to private prepared input and ledger-only inference."""
from dataclasses import dataclass
from copy import deepcopy
from pathlib import Path

from models.schemas import AnnouncementContent, ExtractionResult
from models.inference import precise_json_loads
from data_loader.attachment_parser import AttachmentParseOutcome
from .contracts import content_sha256


@dataclass(frozen=True)
class ParsedAnnouncement:
    content: AnnouncementContent
    attachments: AttachmentParseOutcome
    input_fingerprint: str


@dataclass(frozen=True)
class EntityStepResult:
    result: ExtractionResult
    diagnostics: dict
    stage_status: str


def source_has_targets(content):
    from tools.task1_standard_export.standard_export import classify
    if classify(content.title,content.html_text)=='intention_only':
        return False
    if "主要标的信息" not in content.html_text:
        return False
    for table in content.html_tables:
        if len(table) < 2:
            continue
        header = table[0]
        columns = [index for index, value in enumerate(header) if any(name in value for name in
                   ("货物名称", "服务名称", "标的名称", "产品名称"))]
        if len(columns) == 1 and any(len(row) > columns[0] and row[columns[0]].strip()
                and row[columns[0]].strip() not in {"详见附件", "见附件"} for row in table[1:]):
            return True
    return False


class _PreparedHTML:
    def __init__(self, content):
        self.content = content
    def parse_file(self, path):
        return self.content.model_copy(deep=True)


class _PreparedAttachments:
    def __init__(self, attachments):
        self.attachments = attachments
    def parse_zip_with_status(self, path, aid):
        return deepcopy(self.attachments)


class _RecordingTransport:
    def __init__(self, transport):
        self.transport = transport
        self.task_id = transport.task_id
        self.responses = []
    def __call__(self, request):
        response = self.transport(request)
        self.responses.append((request.layer, response.content))
        return response


def _schema_valid(layer, response):
    try:
        value = response.strip()
        if value.startswith("```") and value.endswith("```"):
            value = value[value.find("\n") + 1:-3].strip()
        data = precise_json_loads(value)
        if not isinstance(data, dict):
            return False
        if layer == "extra":
            return "error" not in data
        key = "detail_entities" if layer == "details" and "detail_entities" in data else "entities"
        if key not in data or not isinstance(data[key], list):
            return False
        for item in data[key]:
            if not isinstance(item, dict):
                return False
            for name in ("product_name", "category", "brand", "spec_model", "unit_price", "quantity", "total_price"):
                if isinstance(item.get(name), (dict, list, bool)):
                    return False
        return True
    except (ValueError, TypeError):
        return False


def run_entity_step(lease, prepared, parsed, transport, paths):
    from pipeline import EntityExtractionPipeline
    from output.writer import ResultWriter
    recorded = _RecordingTransport(transport)
    pipeline = EntityExtractionPipeline(
        llm_provider=getattr(transport, "provider", "deepseek"), model=getattr(transport, "model", None),
        extract_extra=False, db_enabled=False, max_workers=1,
        writer=ResultWriter(paths.extraction), progress_path=paths.extraction / "progress.txt",
        call_transport=recorded,
        html_parser_factory=lambda: _PreparedHTML(parsed.content),
        attachment_parser_factory=lambda: _PreparedAttachments(parsed.attachments))
    result = pipeline.process_single_file(str(prepared.html_path), run_mode="both")
    # Extra is separate so a legal zero-target source still has honest relation candidates.
    extra = pipeline.extractor.extract_extra_info(parsed.content.full_text, prepared.announcement_id)
    extra_valid = isinstance(extra, dict) and "error" not in extra
    if extra_valid:
        result.extra_info.update(extra)
    schema_valid = all(_schema_valid(layer, response) for layer, response in recorded.responses if layer != "extra")
    gap = not result.entities and source_has_targets(parsed.content)
    parser_partial = bool(parsed.attachments.failed_files)
    detail_partial = bool(result.detail_error_message) or result.detail_status in {"unreadable", "extraction_failed"}
    if schema_valid and not gap and not result.entities:
        # Empty is legal only with a complete schema and no explicitly published targets.
        result.success, result.error_message = True, ""
    if not schema_valid or gap:
        result.success = False
        result.error_message = "invalid_model_schema" if not schema_valid else "source_target_coverage_gap"
    state = "failed" if not result.success else ("partial" if parser_partial or detail_partial or not extra_valid else "complete")
    diagnostics = {"schema_valid": schema_valid, "extra_valid": extra_valid, "coverage_gap": gap,
                   "parser_partial": parser_partial, "detail_partial": detail_partial}
    return EntityStepResult(result, diagnostics, state)
