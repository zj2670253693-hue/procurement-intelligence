"""Base function prose cleanup must not affect specs, details or row identity."""

import json
import pytest

from extractor.llm_extractor import LLMExtractor
from tests.test_legacy_base_layer import _pipeline, _RecordingExtractor, _write_announcement
from tests.test_dual_layer_pipeline import make_pipeline, RecordingExtractor, write_html, write_attachment_zip


def source(description="基本信息采集，包括编辑、查询及报告导出。", *, title="2.用户管理", other="", attached="", vertical=False):
    table = "采购标的 | 服务范围 | 服务要求\n虚构平台 | " + title + ":" + description + " | 按约履行" + other
    body_table = table.replace(" | ", "\n") if vertical else table
    return ("二、项目名称:虚构项目\n三、中标（成交）信息\n供应商名称:虚构供应商\n中标（成交）金额:100元\n"
            "四、主要标的信息\n" + body_table + "\n五、评审专家\n"
            "--- HTML表格1 ---\n公告信息:\n采购项目名称 | 虚构项目\n品目 | 服务/其他服务\n"
            "--- HTML表格2 ---\n" + table
            + ("\n--- 附件:说明.txt ---\n" + attached if attached else ""))


def consume(monkeypatch, text, spec="基本信息采集，包括编辑、查询及报告导出。", mode="legacy", name="虚构平台 2.用户管理"):
    extractor = object.__new__(LLMExtractor)
    row = {"product_name": name, "category": "服务/其他服务", "brand": "虚构供应商", "spec_model": spec,
           "unit_price": "100元", "quantity": "1项", "total_price": "100元", "package_name": "包甲", "source_page": "8"}
    key = "detail_entities" if mode == "details" else "entities"
    monkeypatch.setattr(extractor, "_call_llm", lambda *args: json.dumps({key: [row]}, ensure_ascii=False))
    if mode == "details":
        rows, status, error = extractor.extract_details(texts={"说明.txt": text}, tables={})
        assert status == "available", error
        return rows[0]
    result = extractor.extract(text) if mode == "legacy" else extractor.extract_summary(text, has_tables=True, detail_status="available")
    assert result.success, result.error_message
    return result.entities[0]


@pytest.mark.parametrize("mode", ["legacy", "summary"])
@pytest.mark.parametrize("description", ["基本信息采集，包括编辑、查询及报告导出。", "基于GIS技术展示风险点位，包括检索及导出。",
                                       "聚焦事故报告，支持同类事故比对分析与预警。"])
def test_complete_numbered_function_body_is_not_a_base_spec(monkeypatch, mode, description):
    row = consume(monkeypatch, source(description), description, mode)
    assert row.model_dump() == {"product_name": "虚构平台 2.用户管理", "category": "服务/其他服务", "brand": "虚构供应商",
                                "spec_model": "", "unit_price": "100元", "quantity": "1项", "total_price": "100元"}


@pytest.mark.parametrize("mode", ["legacy", "summary"])
@pytest.mark.parametrize("vertical", [False, True])
def test_explanatory_heading_fields_can_locate_one_whole_function_body(monkeypatch, mode, vertical):
    spec = "包括查询用户信息，支持编辑与报告导出。"
    text = source(spec, title="２．用户管理（名称／内容等）", vertical=vertical)
    assert consume(monkeypatch, text, spec, mode).spec_model == ""


@pytest.mark.parametrize("description", [
    "基本信息采集，包括查询，软件版本V2.0。", "基本信息采集，包括查询，配置32GB内存。",
    "基本信息采集，包括查询，带宽100Mbps。", "基本信息采集，包括查询，在线率≥98%。",
    "基本信息采集，包括查询，响应时间小于一秒。", "基本信息采集，包括查询，支持1000个并发用户。",
    "基本信息采集，包括查询，采用1920×1080屏幕。", "基本信息采集，包括查询，型号EdgeX200。",
    "包含配置风险采集表信息、隐患信息、排查信息等标准库。",
])
def test_real_or_uncertain_attributes_are_preserved(monkeypatch, description):
    assert consume(monkeypatch, source(description), description).spec_model == description


@pytest.mark.parametrize("tail", ["名称/型号等", "32GB内存", "高性能版"])
def test_non_explanatory_or_attribute_heading_parentheses_are_not_removed(monkeypatch, tail):
    spec = "包括查询用户信息，支持编辑与报告导出。"
    assert consume(monkeypatch, source(spec, title="2.用户管理(" + tail + ")"), spec).spec_model == spec


def test_two_rows_matching_the_same_reduced_heading_are_ambiguous(monkeypatch):
    text = source(other="\n虚构平台 | 2.用户管理(名称/内容等):其他数据查询与编辑。 | 按约履行")
    assert consume(monkeypatch, text).spec_model != ""


def test_only_a_fragment_of_the_function_body_is_not_cleared(monkeypatch):
    spec = "基本信息采集，包括编辑、查询及报告导出。"
    assert consume(monkeypatch, source(spec + "另有按需工作安排。"), spec).spec_model == spec


@pytest.mark.parametrize("attached", ["未知说明:基本信息采集，包括编辑、查询及报告导出。",
    "服务名称 | 规格型号\n虚构平台 2.用户管理 | 星河版", "服务名称 | 服务范围\n虚构平台 2.用户管理 | 其他查询功能。"])
def test_unknown_or_competing_attachment_evidence_prevents_clear(monkeypatch, attached):
    assert consume(monkeypatch, source(attached=attached)).spec_model != ""


@pytest.mark.parametrize("extra", [
    "虚构平台 | 2.用户管理:支持其他查询。 | 按约履行",
    "虚构平台 | 2.用户管理:基本信息采集，包括编辑、查询及报告导出。 | 按约履行",
    "服务名称 | 规格型号\n虚构平台 2.用户管理 | 星河",
    "服务名称\n规格型号\n虚构平台 2.用户管理\n星河",
    "虚构平台 | 2.用户管理:支持其他查询。",
])
def test_body_only_competitor_or_unknown_layout_prevents_clear(monkeypatch, extra):
    text = source().replace("\n五、评审专家", "\n" + extra + "\n五、评审专家")
    assert consume(monkeypatch, text).spec_model != ""


@pytest.mark.parametrize("description", [
    "基本信息采集，包括编辑与查询，可存储一万条数据。",
    "查询结果准确率不低于百分之九十九。",
    "基本信息采集，包括查询，可容纳两千名用户。",
    "基本信息采集，包括查询，支持十万条记录。",
])
def test_chinese_capacity_or_performance_is_preserved(monkeypatch, description):
    assert consume(monkeypatch, source(description), description).spec_model == description


@pytest.mark.parametrize("description", [
    "基本信息采集，支持每秒查询500次。", "查询在一秒内完成。", "采集频次为每秒一百次。",
    "基本信息采集，包括查询，支持每分钟十万笔交易。", "基本信息采集，检索时限为五分钟。",
])
def test_rate_or_time_constraint_is_preserved(monkeypatch, description):
    assert consume(monkeypatch, source(description), description).spec_model == description


@pytest.mark.parametrize("description", [
    "基本信息采集，每天最多查询一百次。", "基本信息采集，支持每日查询100次。", "查询在半秒内完成。",
    "基本信息采集，包括查询，可识别三米外目标。", "基本信息采集，查询在零点五秒内完成。",
    "基本信息采集，支持对每一条隐患建立跟踪记录。",
])
def test_daily_fractional_or_unknown_quantitative_prose_is_preserved(monkeypatch, description):
    assert consume(monkeypatch, source(description), description).spec_model == description


@pytest.mark.parametrize("description", [
    "基本信息采集，支持500路视频采集。", "基本信息采集，最多管理500用户。",
    "基本信息采集，支持20节点数据采集。", "基本信息采集，管理上限500。",
    "基本信息采集，最多管理百万用户。",
])
def test_unknown_numeric_capacity_in_function_body_is_preserved(monkeypatch, description):
    assert consume(monkeypatch, source(description), description).spec_model == description


@pytest.mark.parametrize("description", [
    "基本信息采集，支持三路视频采集。", "基本信息采集，支持四用户查询。",
    "基本信息采集，支持七节点数据采集。", "基本信息采集，支持叁路视频采集。",
])
def test_single_chinese_or_formal_numeral_capacity_is_preserved(monkeypatch, description):
    assert consume(monkeypatch, source(description), description).spec_model == description


@pytest.mark.parametrize("heading", ["5.技术要求", "5.合同履行期限", "5.未知章节", "五、技术要求"])
def test_unknown_outer_heading_cannot_certify_an_item_table(monkeypatch, heading):
    text = source().replace("四、主要标的信息", "4.主要标的信息\n" + heading)
    assert consume(monkeypatch, text).spec_model != ""


@pytest.mark.parametrize("extra", [
    "备注 | 5.技术要求:另行规定",
    "服务名称 | 服务范围\n其他平台 | 5.技术要求:另行规定",
])
@pytest.mark.parametrize("after_header", [False, True])
def test_global_numbered_cell_occurrence_cannot_authorize_an_outer_chapter(monkeypatch, extra, after_header):
    text = source()
    if after_header:
        text = text.replace("采购标的 | 服务范围 | 服务要求\n", "采购标的 | 服务范围 | 服务要求\n5.技术要求:另行规定\n", 1)
    else:
        text = text.replace("四、主要标的信息", "4.主要标的信息\n5.技术要求:另行规定")
    text += "\n--- HTML表格3 ---\n" + extra
    assert consume(monkeypatch, text).spec_model != ""


@pytest.mark.parametrize("vertical", [False, True])
@pytest.mark.parametrize("prefix", ["1.虚构平台", "5.评审专家"])
def test_numbered_owner_competitor_cannot_disappear_as_a_chapter(monkeypatch, vertical, prefix):
    text = source(vertical=vertical).replace("虚构平台", prefix)
    extra = prefix + " | 2.用户管理:支持其他查询。 | 按约履行"
    if vertical:
        extra = extra.replace(" | ", "\n")
    text = text.replace("五、评审专家", extra + "\n五、评审专家")
    assert consume(monkeypatch, text, name=prefix + " 2.用户管理").spec_model != ""


@pytest.mark.parametrize("title", ["5.评审专家管理", "6.代理服务管理", "7.公告期限管理"])
def test_function_cell_prefix_is_not_a_plain_closing_heading(monkeypatch, title):
    spec = "基本信息采集，包括编辑、查询及报告导出。"
    text = source(spec, title=title, vertical=True)
    text = text.replace("采购标的\n服务范围\n服务要求\n虚构平台\n" + title + ":" + spec,
                        "服务范围\n采购标的\n服务要求\n" + title + ":" + spec + "\n虚构平台")
    text = text.replace("采购标的 | 服务范围 | 服务要求\n虚构平台 | " + title + ":" + spec,
                        "服务范围 | 采购标的 | 服务要求\n" + title + ":" + spec + " | 虚构平台")
    text = text.replace("五、评审专家", title + ":支持其他查询。\n虚构平台\n按约履行\n五、评审专家")
    assert consume(monkeypatch, text, name="虚构平台 " + title).spec_model != ""


@pytest.mark.parametrize("label", ["规格型号(说明)", "软件版本（供应商填写）", "规格参数"])
def test_qualified_or_unknown_spec_like_header_prevents_clear(monkeypatch, label):
    text = source().replace("采购标的 | 服务范围 | 服务要求", "采购标的 | 服务范围 | 服务要求 | " + label)
    text = text.replace(" | 按约履行", " | 按约履行 | 星河")
    assert consume(monkeypatch, text).spec_model != ""


@pytest.mark.parametrize("attached", [
    "产品名称 | 型号\n虚构平台 2.用户管理 | 星河",
    "服务名称 | 服务范围\n虚构平台 | 2.用户管理(名称/内容等):其他查询功能。",
    "产品名称\n型号\n虚构平台 2.用户管理\n星河",
])
def test_attachment_uses_same_owner_names_and_heading_aliases(monkeypatch, attached):
    assert consume(monkeypatch, source(attached=attached)).spec_model != ""


@pytest.mark.parametrize("transform", [
    lambda t: t.replace("二、项目名称:虚构项目", "二、项目名称:另一个项目"),
    lambda t: t.replace("--- HTML表格1 ---", "--- 来源不明 ---"),
    lambda t: t.replace("虚构平台 | 2.用户管理:", "其他平台 | 2.用户管理:", 1),
    lambda t: t.replace("四、主要标的信息", "四、采购需求"),
])
def test_scope_or_body_owner_must_be_proven(monkeypatch, transform):
    assert consume(monkeypatch, transform(source())).spec_model != ""


def test_detail_entrypoint_keeps_the_old_guard_and_metadata(monkeypatch):
    row = consume(monkeypatch, source(), mode="details")
    assert row.spec_model == "基本信息采集，包括编辑、查询及报告导出。"
    assert (row.package_name, row.source_file, row.source_page) == ("包甲", "说明.txt", "8")


@pytest.mark.parametrize("label,value", [("规格型号", "星河版"), ("软件版本", "V2.0"), ("规格型号", "基本信息采集，包括编辑、查询及报告导出。")])
def test_explicit_spec_column_prevents_clearing(monkeypatch, label, value):
    text = source().replace("采购标的 | 服务范围 | 服务要求", "采购标的 | 服务范围 | 服务要求 | " + label)
    text = text.replace(" | 按约履行", " | 按约履行 | " + value)
    assert consume(monkeypatch, text).spec_model != ""


def test_duplicate_serialized_rows_remain_ambiguous(monkeypatch):
    text = source()
    last = text.rsplit("\n", 1)[-1]
    assert consume(monkeypatch, text + "\n" + last).spec_model != ""


def test_truncated_competing_row_prevents_unique_mapping(monkeypatch):
    text = source(other="\n虚构平台 | 2.用户管理:其他查询功能。")
    assert consume(monkeypatch, text).spec_model != ""


def test_generic_module_name_without_the_function_heading_is_preserved(monkeypatch):
    assert consume(monkeypatch, source(), name="虚构平台").spec_model != ""


def test_unknown_function_semantics_are_preserved(monkeypatch):
    assert consume(monkeypatch, source("常规办理。"), "常规办理。").spec_model == "常规办理。"


def test_helper_preserves_detailed_type_metadata_input_and_other_fields():
    from extractor.base_function_spec import clear_base_function_specs
    from models.schemas import DetailedEntity
    original = DetailedEntity(product_name="虚构平台 2.用户管理", spec_model="基本信息采集，包括编辑、查询及报告导出。",
                              brand="虚构供应商", package_name="包甲", source_file="来源", source_page="8")
    before = original.model_dump()
    cleaned = clear_base_function_specs([original], source_text=source())[0]
    assert original.model_dump() == before and type(cleaned) is DetailedEntity and cleaned is not original
    assert cleaned.model_dump() == {**before, "spec_model": ""}


def test_helper_does_not_infer_absence_from_a_missing_source_value():
    from extractor.base_function_spec import clear_base_function_specs
    from models.schemas import ExtractedEntity
    original = ExtractedEntity(product_name="虚构平台 2.用户管理", spec_model="未在当前缓存出现的说明")
    assert clear_base_function_specs([original], source_text=source())[0].spec_model == original.spec_model


def test_clearing_does_not_trigger_another_dedup(monkeypatch):
    extractor = object.__new__(LLMExtractor)
    rows = [{"product_name": "虚构平台 2.用户管理", "spec_model": s, "brand": "虚构供应商"}
            for s in ("基本信息采集，包括编辑、查询及报告导出。", "")]
    monkeypatch.setattr(extractor, "_call_llm", lambda *args: json.dumps({"entities": rows}, ensure_ascii=False))
    result = extractor.extract(source())
    assert result.success and len(result.entities) == 2
    assert [r.spec_model for r in result.entities] == ["", ""]


def test_base_resume_invalidates_new_function_guard_version(tmp_path, monkeypatch):
    import pipeline as module
    runner = _pipeline(tmp_path, _RecordingExtractor())
    html = _write_announcement(tmp_path, "fictional-function-legacy")
    first = runner.process_single_file(str(html), run_mode="legacy")
    monkeypatch.setattr(module, "BASE_FUNCTION_SPEC_RULE_VERSION", "test-next", raising=False)
    second = runner.process_single_file(str(html), run_mode="legacy", previous_result=first, resume=True)
    assert first.summary_input_hash != second.summary_input_hash


def test_base_version_leaves_detail_resume_unchanged(tmp_path, monkeypatch):
    import pipeline as module
    runner = make_pipeline(tmp_path, RecordingExtractor())
    html = write_html(tmp_path, "fictional-function-both", "成交金额100元")
    write_attachment_zip(html, "测试设备 | 100元 | 1台")
    first = runner.process_single_file(str(html), run_mode="both")
    monkeypatch.setattr(module, "BASE_FUNCTION_SPEC_RULE_VERSION", "test-next", raising=False)
    second = runner.process_single_file(str(html), run_mode="both", previous_result=first, resume=True)
    assert first.summary_input_hash != second.summary_input_hash
    assert first.detail_input_hash == second.detail_input_hash
