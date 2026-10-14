import pytest

from models.result_io import parse_extraction_result, serialize_extraction_result
from models.schemas import (
    DetailedEntity,
    ExtractedEntity,
    ExtractionResult,
    deduplicate_detail_entities,
)


def test_legacy_result_loads_with_unresolved_detail_status():
    result = parse_extraction_result(
        {
            "announcement_id": "fictional-001",
            "success": True,
            "entities": [{"product_name": "虚构设备"}],
        }
    )

    assert result.detail_entities == []
    assert result.detail_status is None
    assert result.schema_version == 1
    assert result.summary_input_hash == ""
    assert result.detail_input_hash == ""


def test_dual_layer_result_round_trips_without_mixing_layers():
    result = ExtractionResult(
        announcement_id="fictional-002",
        entities=[ExtractedEntity(product_name="采购包一")],
        detail_entities=[
            DetailedEntity(
                product_name="设备甲",
                source_file="明细.pdf",
                source_page="",
            )
        ],
        detail_status="available",
        schema_version=2,
        summary_input_hash="summary-hash",
        detail_input_hash="detail-hash",
    )

    loaded = parse_extraction_result(serialize_extraction_result(result))

    assert [item.product_name for item in loaded.entities] == ["采购包一"]
    assert [item.product_name for item in loaded.detail_entities] == ["设备甲"]
    assert loaded.detail_entities[0].source_file == "明细.pdf"
    assert loaded.detail_entities[0].source_page == ""
    assert loaded.detail_status == "available"
    assert loaded.summary_input_hash == "summary-hash"
    assert loaded.detail_input_hash == "detail-hash"


def test_legacy_status_must_be_resolved_before_publish():
    result = parse_extraction_result(
        {
            "announcement_id": "fictional-003",
            "success": True,
            "entities": [],
        }
    )

    with pytest.raises(ValueError, match="待迁移"):
        serialize_extraction_result(result, require_resolved=True)


def test_detail_deduplication_is_package_scoped_and_keeps_first_source():
    items = [
        DetailedEntity(
            package_name="采购包一",
            product_name="设备 甲",
            quantity="1 台",
            source_file="首次.pdf",
        ),
        DetailedEntity(
            package_name="采购包一",
            product_name="设备甲",
            quantity="1台",
            source_file="重复.pdf",
        ),
        DetailedEntity(
            package_name="采购包二",
            product_name="设备甲",
            quantity="1台",
            source_file="另一包.pdf",
        ),
    ]

    result = deduplicate_detail_entities(items)

    assert len(result) == 2
    assert result[0].source_file == "首次.pdf"
    assert result[1].package_name == "采购包二"


def test_serialized_payload_uses_stable_public_keys_without_raw_response():
    payload = serialize_extraction_result(
        ExtractionResult(
            announcement_id="fictional-004",
            entities=[ExtractedEntity(product_name="采购包一")],
            detail_status="no_attachment",
            raw_response="private model response",
            error_message="",
        ),
        require_resolved=True,
    )

    assert payload == {
        "schema_version": 2,
        "announcement_id": "fictional-004",
        "success": True,
        "error": "",
        "entities": [
            {
                "product_name": "采购包一",
                "category": "",
                "brand": "",
                "spec_model": "",
                "unit_price": "",
                "quantity": "",
                "total_price": "",
            }
        ],
        "detail_entities": [],
        "detail_status": "no_attachment",
        "detail_error_message": "",
        "summary_input_hash": "",
        "detail_input_hash": "",
        "extra_info": {},
    }
