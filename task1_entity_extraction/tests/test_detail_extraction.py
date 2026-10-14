import json

from extractor.detail_processing import chunk_attachment_text, migrate_legacy_entities
from extractor.llm_extractor import LLMExtractor
from models.schemas import (
    DetailedEntity,
    ExtractedEntity,
    ExtractionResult,
    deduplicate_detail_entities,
)


def test_legacy_entities_move_to_details_without_becoming_summary():
    old = ExtractionResult(
        announcement_id="fictional-001",
        schema_version=1,
        entities=[ExtractedEntity(product_name="设备甲")],
        detail_status=None,
    )

    migrated = migrate_legacy_entities(old, detail_status="available")

    assert migrated.entities == []
    assert [item.product_name for item in migrated.detail_entities] == ["设备甲"]
    assert migrated.detail_status == "available"
    assert migrated.schema_version == 2


def test_legacy_entities_without_readable_attachment_are_not_detail_candidates():
    old = ExtractionResult(
        announcement_id="fictional-002",
        schema_version=1,
        entities=[ExtractedEntity(product_name="原基础结果")],
        detail_status=None,
    )

    migrated = migrate_legacy_entities(old, detail_status="no_attachment")

    assert migrated.entities == []
    assert migrated.detail_entities == []
    assert migrated.detail_status == "no_attachment"
    assert migrated.schema_version == 2


def test_duplicate_detail_across_sources_keeps_first_source():
    items = deduplicate_detail_entities(
        [
            DetailedEntity(
                package_name="包一",
                product_name="设备甲",
                source_file="A.pdf",
            ),
            DetailedEntity(
                package_name="包一",
                product_name="设备甲",
                source_file="B.pdf",
            ),
        ]
    )

    assert len(items) == 1
    assert items[0].source_file == "A.pdf"


def test_chunk_attachment_text_respects_limit_and_overlap():
    text = "第一行内容\n第二行内容\n第三行内容\n第四行内容"

    chunks = chunk_attachment_text(text, max_length=15, overlap=4)

    assert len(chunks) > 1
    assert all(0 < len(chunk) <= 15 for chunk in chunks)
    assert "第一行内容" in chunks[0]
    assert "第四行内容" in chunks[-1]


def test_extract_details_merges_files_and_deduplicates_cross_file_result(monkeypatch):
    extractor = object.__new__(LLMExtractor)
    first = [
        {"package_name": "包一", "product_name": f"设备-{index:02d}"}
        for index in range(1, 26)
    ]
    second = [
        {"package_name": "包一", "product_name": f"设备-{index:02d}"}
        for index in range(25, 51)
    ]

    def fake_call(system_prompt, user_prompt):
        payload = first if "A.txt" in user_prompt else second
        return json.dumps({"detail_entities": payload}, ensure_ascii=False)

    monkeypatch.setattr(extractor, "_call_llm", fake_call)

    items, status, error = extractor.extract_details(
        texts={"A.txt": "第一批明细", "B.txt": "第二批明细"},
        tables={},
        announcement_id="fictional-003",
    )

    assert status == "available"
    assert error == ""
    assert len(items) == 50
    assert items[0].source_file == "A.txt"
    duplicate = next(item for item in items if item.product_name == "设备-25")
    assert duplicate.source_file == "A.txt"


def test_extract_details_keeps_successful_file_when_another_file_fails(monkeypatch):
    extractor = object.__new__(LLMExtractor)

    def fake_call(system_prompt, user_prompt):
        if "损坏.txt" in user_prompt:
            raise RuntimeError("fictional failure")
        return json.dumps(
            {"detail_entities": [{"product_name": "设备甲"}]},
            ensure_ascii=False,
        )

    monkeypatch.setattr(extractor, "_call_llm", fake_call)

    items, status, error = extractor.extract_details(
        texts={"可读.txt": "设备甲", "损坏.txt": "错误内容"},
        tables={},
        announcement_id="fictional-004",
    )

    assert status == "available"
    assert [item.product_name for item in items] == ["设备甲"]
    assert items[0].source_file == "可读.txt"
    assert "损坏.txt" in error


def test_extract_details_reports_failure_when_every_chunk_fails(monkeypatch):
    extractor = object.__new__(LLMExtractor)
    monkeypatch.setattr(
        extractor,
        "_call_llm",
        lambda system_prompt, user_prompt: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    items, status, error = extractor.extract_details(
        texts={"失败.txt": "无法提取"},
        tables={},
        announcement_id="fictional-005",
    )

    assert items == []
    assert status == "extraction_failed"
    assert "失败.txt" in error
