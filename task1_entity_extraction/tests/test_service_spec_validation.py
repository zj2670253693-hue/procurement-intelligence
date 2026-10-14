"""Clear proven service prose only; ambiguous rows and attributes survive."""

import json

import pytest

from extractor.llm_extractor import LLMExtractor
from extractor.spec_rules import sanitize_spec_entities
from models.schemas import DetailedEntity, ExtractedEntity


def _extract(monkeypatch, mode, name, spec, source):
    source += "\n成交金额：100元"
    extractor = object.__new__(LLMExtractor)
    row = {"product_name": name, "category": "", "brand": "测试供应商",
           "spec_model": spec, "unit_price": "100元", "quantity": "1项",
           "total_price": "100元", "package_name": "包1", "source_page": "3"}
    key = "detail_entities" if mode == "details" else "entities"
    monkeypatch.setattr(extractor, "_call_llm", lambda *args: json.dumps({key: [row]}, ensure_ascii=False))
    if mode == "legacy":
        result = extractor.extract(source)
        assert result.success, result.error_message
        return result.entities[0]
    if mode == "summary":
        result = extractor.extract_summary(source, has_tables=True, detail_status="available")
        assert result.success, result.error_message
        return result.entities[0]
    rows, status, error = extractor.extract_details(texts={"清单.txt": source}, tables={})
    assert status == "available", error
    return rows[0]


@pytest.mark.parametrize("mode", ["legacy", "summary", "details"])
def test_unique_service_scope_copy_is_cleared_in_all_entrypoints(monkeypatch, mode):
    spec = "提供操作日志查询，支持记录用户登录状态。"
    source = "采购标的 | 服务范围 | 服务要求\n日志管理服务 | " + spec + " | 按约履行"
    row = _extract(monkeypatch, mode, "日志管理服务", spec, source)
    assert row.spec_model == ""
    assert (row.product_name, row.brand, row.unit_price, row.quantity, row.total_price) == (
        "日志管理服务", "测试供应商", "100元", "1项", "100元",
    )


@pytest.mark.parametrize("mode", ["legacy", "summary", "details"])
def test_unique_service_columns_concatenation_is_not_a_spec(monkeypatch, mode):
    source = ("采购标的 | 服务范围 | 服务要求 | 服务时间 | 服务标准\n"
              "团体健康保险 | 80人 | 按照采购人要求进行服务 | 投保期365个自然日 | 符合国家行业标准")
    spec = "80人，按照采购人要求进行服务，投保期365个自然日，符合国家行业标准"
    assert _extract(monkeypatch, mode, "团体健康保险", spec, source).spec_model == ""


@pytest.mark.parametrize("mode", ["legacy", "summary", "details"])
def test_function_name_and_scope_heading_can_uniquely_identify_row(monkeypatch, mode):
    spec = "包括操作日志查询，支持记录登录状态。"
    source = ("采购标的 | 服务范围 | 服务要求\n"
              "资源管理 | 2.日志管理：" + spec + " | 按约履行\n"
              "资源管理 | 1.用户管理：提供用户权限管理功能。 | 按约履行")
    assert _extract(monkeypatch, mode, "资源管理 2.日志管理", spec, source).spec_model == ""


@pytest.mark.parametrize("mode", ["legacy", "summary", "details"])
def test_same_named_rows_remain_ambiguous_even_if_only_one_spec_text_matches(monkeypatch, mode):
    spec = "提供北区日常巡查服务。"
    source = ("采购标的 | 服务范围 | 服务要求\n维护服务 | " + spec + " | 按约履行\n"
              "维护服务 | 提供南区日常巡查服务。 | 按约履行")
    assert _extract(monkeypatch, mode, "维护服务", spec, source).spec_model == spec


@pytest.mark.parametrize("spec", [
    "提供日志管理功能，软件版本V2.0。", "提供线路租赁服务，带宽100Mbps。",
    "支持巡检，型号X200。", "提供维护服务，配置32GB内存。",
    "提供数据存储服务，容量一太字节。", "提供设备维护，尺寸四十厘米。",
    "提供定制化服务。", "包括设备维护，采用25kg/袋包装。",
    "提供日志管理功能，采用EdgeX200。", "支持图像处理，分辨率1920×1080。",
    "提供服务器运维，CPU为八核。", "支持巡检，设备为全铜防腐结构。",
])
def test_service_column_with_real_or_uncertain_attribute_is_preserved(spec):
    source = "采购标的 | 服务范围\n维护服务 | " + spec
    row = ExtractedEntity(product_name="维护服务", spec_model=spec)
    assert sanitize_spec_entities([row], source_text=source)[0].spec_model == spec


@pytest.mark.parametrize("spec", ["常规", "人工智能", "标准化实施"])
def test_unknown_service_cell_semantics_are_not_enough_to_clear(spec):
    source = "采购标的 | 服务范围\n咨询服务 | " + spec
    row = ExtractedEntity(product_name="咨询服务", spec_model=spec)
    assert sanitize_spec_entities([row], source_text=source)[0].spec_model == spec


def test_declared_spec_column_prevents_clearing_even_when_prose_is_repeated():
    spec = "提供按需查询与统计功能。"
    source = "采购标的 | 服务范围 | 规格型号\n查询服务 | " + spec + " | " + spec
    row = ExtractedEntity(product_name="查询服务", spec_model=spec)
    assert sanitize_spec_entities([row], source_text=source)[0].spec_model == spec


def test_nonempty_actual_spec_in_current_row_prevents_absence_inference():
    spec = "提供日志查询功能。"
    source = "采购标的 | 服务范围 | 软件版本\n日志服务 | " + spec + " | 星河版"
    row = ExtractedEntity(product_name="日志服务", spec_model=spec)
    assert sanitize_spec_entities([row], source_text=source)[0].spec_model == spec


@pytest.mark.parametrize("source", [
    "采购标的 | 服务范围\n其他服务 | 提供日志查询功能。",
    "提供日志查询功能。",
    "名称：日志服务\n服务范围：提供日志查询功能。",
    "采购标的 | 服务范围\n日志服务 | 提供日志查询功能，同时采用专用规格。",
    "采购标的 | 服务范围 | 服务要求\n日志服务 | 提供日志查询功能。",
    "采购标的 | 服务范围 | 服务范围\n日志服务 | 提供日志查询功能。 | 按约履行",
])
def test_missing_malformed_or_partial_evidence_keeps_spec(source):
    spec = "提供日志查询功能。"
    row = ExtractedEntity(product_name="日志服务", spec_model=spec)
    assert sanitize_spec_entities([row], source_text=source)[0].spec_model == spec


@pytest.mark.parametrize("status", [None, "missing", "unreadable"])
def test_source_provenance_cleanup_is_not_an_attachment_absence_inference(status):
    spec = "提供日志查询功能。"
    source = "采购标的 | 服务范围\n日志服务 | " + spec
    row = ExtractedEntity(product_name="日志服务", spec_model=spec)
    assert sanitize_spec_entities([row], source_text=source, detail_status=status)[0].spec_model == ""


def test_clearing_service_copy_preserves_original_objects_metadata_and_order():
    scope = "提供日志查询功能。"
    source = "服务名称 | 服务范围\n日志服务 | " + scope + "\n权限服务 | 提供用户权限管理功能。"
    rows = [DetailedEntity(product_name="日志服务", spec_model=scope, brand="甲公司",
                           package_name="包1", source_file="清单.txt", source_page="3"),
            DetailedEntity(product_name="权限服务", spec_model="提供用户权限管理功能。",
                           brand="乙公司", package_name="包2", source_file="清单.txt", source_page="4")]
    cleaned = sanitize_spec_entities(rows, source_text=source)
    assert [row.spec_model for row in cleaned] == ["", ""]
    assert [row.spec_model for row in rows] == [scope, "提供用户权限管理功能。"]
    assert [(row.product_name, row.brand, row.package_name, row.source_file, row.source_page)
            for row in cleaned] == [("日志服务", "甲公司", "包1", "清单.txt", "3"),
                                    ("权限服务", "乙公司", "包2", "清单.txt", "4")]


@pytest.mark.parametrize("spec", [
    "支持1000个并发用户，提供日志查询功能。",
    "提供千兆专线租赁服务。", "提供星河版日志软件，支持查询功能。",
    "提供数据查询功能，响应时间小于一秒。",
    "支持巡检服务，采用木质机箱。",
])
def test_less_obvious_real_or_uncertain_attributes_also_stay(spec):
    source = "采购标的 | 服务范围\n维护服务 | " + spec
    row = ExtractedEntity(product_name="维护服务", spec_model=spec)
    assert sanitize_spec_entities([row], source_text=source)[0].spec_model == spec


def test_same_named_row_in_another_spec_table_prevents_unique_mapping():
    spec = "提供日志查询功能。"
    source = ("采购标的 | 服务范围\n日志服务 | " + spec + "\n\n"
              "--- 附件：明细表 ---\n服务名称 | 规格型号\n日志服务 | 星河版")
    row = ExtractedEntity(product_name="日志服务", spec_model=spec)
    assert sanitize_spec_entities([row], source_text=source)[0].spec_model == spec


def test_unrelated_item_in_another_spec_table_does_not_block_unique_mapping():
    spec = "提供日志查询功能。"
    source = ("采购标的 | 服务范围\n日志服务 | " + spec + "\n\n"
              "--- 附件：明细表 ---\n服务名称 | 规格型号\n网络服务 | 星河版")
    row = ExtractedEntity(product_name="日志服务", spec_model=spec)
    assert sanitize_spec_entities([row], source_text=source)[0].spec_model == ""


@pytest.mark.parametrize("spec", [
    "提供100Mbps上下行对称网络服务。", "提供25kg大米配送服务。",
])
def test_technical_units_are_protected_when_immediately_followed_by_chinese(spec):
    source = "采购标的 | 服务范围\n维护服务 | " + spec
    row = ExtractedEntity(product_name="维护服务", spec_model=spec)
    assert sanitize_spec_entities([row], source_text=source)[0].spec_model == spec


def test_a_truncated_same_named_row_makes_an_otherwise_complete_table_ambiguous():
    spec = "提供日志查询功能。"
    source = "采购标的 | 服务范围 | 服务要求\n日志服务 | " + spec + " | 按约履行\n日志服务 | 其他范围"
    row = ExtractedEntity(product_name="日志服务", spec_model=spec)
    assert sanitize_spec_entities([row], source_text=source)[0].spec_model == spec


@pytest.mark.parametrize("label", ["版本", "规格型号（软件）"])
def test_nonempty_spec_header_variants_prevent_cleanup(label):
    spec = "提供日志查询功能。"
    source = "采购标的 | 服务范围 | " + label + "\n日志服务 | " + spec + " | 星河版"
    row = ExtractedEntity(product_name="日志服务", spec_model=spec)
    assert sanitize_spec_entities([row], source_text=source)[0].spec_model == spec


@pytest.mark.parametrize("spec", [
    "提供4G网络接入服务。", "提供1080P视频回传服务。",
    "提供100Ｍｂｐｓ上下行对称网络服务。",
])
def test_digit_first_or_fullwidth_technical_attributes_stay(spec):
    source = "采购标的 | 服务范围\n维护服务 | " + spec
    row = ExtractedEntity(product_name="维护服务", spec_model=spec)
    assert sanitize_spec_entities([row], source_text=source)[0].spec_model == spec
