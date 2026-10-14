"""Base-only category corrections require source roles, never gold labels."""

import json
import pytest

from extractor.llm_extractor import LLMExtractor
from tests.test_legacy_base_layer import _pipeline, _RecordingExtractor, _write_announcement
from tests.test_dual_layer_pipeline import make_pipeline, RecordingExtractor, write_html, write_attachment_zip


def metadata(category="服务/其他服务", project="虚构项目", number=1):
    return f"--- HTML表格{number} ---\n公告信息:\n采购项目名称 | {project}\n品目 | {category}"


def source(category="服务/其他服务", *, attachment="", extra=""):
    table = ("序号 | 供应商名称 | 服务名称 | 服务范围 | 服务要求 | 服务时间 | 服务标准\n"
             "1 | 虚构供应商 | 虚构平台维护服务 | 平台维护服务 | 达标 | 一年 | 合格")
    return ("二、项目名称:虚构项目\n三、中标（成交）信息\n供应商名称:虚构供应商\n"
            "中标（成交）金额:100元\n四、主要标的信息\n" + table + "\n五、评审专家\n" + extra
            + metadata(category) + "\n--- HTML表格2 ---\n" + table
            + ("\n--- 附件:说明.txt ---\n" + attachment if attachment else ""))


def consume(monkeypatch, text, category="平台维护服务", mode="legacy", name="虚构平台维护服务"):
    extractor = object.__new__(LLMExtractor)
    row = {"product_name": name, "category": category, "brand": "虚构供应商", "spec_model": "",
           "unit_price": "100元", "quantity": "1项", "total_price": "100元",
           "package_name": "包甲", "source_page": "7"}
    payload = json.dumps({"detail_entities" if mode == "details" else "entities": [row]}, ensure_ascii=False)
    monkeypatch.setattr(extractor, "_call_llm", lambda *args: payload)
    if mode == "details":
        rows, status, error = extractor.extract_details(texts={"说明.txt": text}, tables={})
        assert status == "available", error
        return rows[0]
    result = extractor.extract(text) if mode == "legacy" else extractor.extract_summary(text, has_tables=True, detail_status="available")
    assert result.success, result.error_message
    return result.entities[0]


@pytest.mark.parametrize("mode", ["legacy", "summary"])
def test_proven_service_name_or_scope_is_corrected_to_unique_announcement_category(monkeypatch, mode):
    row = consume(monkeypatch, source(), mode=mode)
    assert row.model_dump() == {"product_name": "虚构平台维护服务", "category": "服务/其他服务",
                                "brand": "虚构供应商", "spec_model": "", "unit_price": "100元",
                                "quantity": "1项", "total_price": "100元"}


@pytest.mark.parametrize("mode", ["legacy", "summary"])
def test_field_heading_must_not_complete_the_previous_field_value(monkeypatch, mode):
    text = ("二、项目名称:虚构项目\n四、主要标的信息\n名称:\n虚构软件运维\n服务范围:\n设备维护\n五、评审专家\n"
            "中标（成交）金额:100元\n" + metadata(""))
    assert consume(monkeypatch, text, "软件运维服务", mode, "虚构软件运维").category == ""


@pytest.mark.parametrize("vertical", [False, True])
def test_table_cells_must_not_complete_each_others_category(monkeypatch, vertical):
    text = source(category="").replace("虚构平台维护服务 | 平台维护服务", "虚构软件运维 | 服务设备")
    if vertical:
        before, marker, rest = text.partition("--- HTML表格1 ---")
        before = before.replace(" | ", "\n")
        text = before + marker + rest
    assert consume(monkeypatch, text, "软件运维服务", name="虚构软件运维").category == ""


@pytest.mark.parametrize("winning", [True, False])
def test_explicit_winning_service_price_label_can_prove_a_name_not_a_category(monkeypatch, winning):
    text = source().replace("虚构平台维护服务 | 平台维护服务", "其他整包服务 | 整包维护")
    text = text.replace("五、评审专家\n", "五、评审专家\n八、其他补充事宜\n"
                        + ("成交单位:\n" if winning else "候选单位:\n") + "虚构平台维护服务单台设备报价:¥100.00\n")
    expected = "服务/其他服务" if winning else "平台维护服务"
    assert consume(monkeypatch, text).category == expected


@pytest.mark.parametrize("text", [
    source(extra="品目名称:平台维护服务\n"),
    source(attachment="品目名称:平台维护服务"),
    source(attachment="类型 | 名称\n平台维护服务 | 虚构平台维护服务"),
    source(attachment="未知说明中提及平台维护服务"),
    source(category=""),
    source(extra="" ).replace("二、项目名称:虚构项目", "二、项目名称:另一个项目"),
    source() + "\n" + metadata("不同品目", number=3),
    source().replace("--- HTML表格1 ---", "--- 来源不明 ---"),
])
def test_true_category_unknown_context_or_conflicting_scope_is_not_overwritten(monkeypatch, text):
    assert consume(monkeypatch, text).category == "平台维护服务"


def test_same_service_phrase_on_detached_row_is_not_a_proven_owner(monkeypatch):
    text = source().replace("虚构平台维护服务 | 平台维护服务", "其他服务 | 平台维护服务", 1)
    assert consume(monkeypatch, text).category == "平台维护服务"


def replace_item_table(text, replacement):
    original = ("序号 | 供应商名称 | 服务名称 | 服务范围 | 服务要求 | 服务时间 | 服务标准\n"
                "1 | 虚构供应商 | 虚构平台维护服务 | 平台维护服务 | 达标 | 一年 | 合格")
    return text.replace(original, replacement)


@pytest.mark.parametrize("header", ["名称 | 品牌", "货物名称 | 型号", "货物名称 | 货物规格型号", "名称 | 数量(台)"])
def test_goods_header_terminates_previous_service_ownership(monkeypatch, header):
    text = replace_item_table(source(), "服务名称 | 服务范围\n其他服务 | 其他范围\n"
                              + header + "\n虚构平台维护服务 | 平台维护服务")
    assert consume(monkeypatch, text).category == "平台维护服务"


@pytest.mark.parametrize("vertical", [False, True])
def test_complete_rows_must_not_complete_each_others_category(monkeypatch, vertical):
    text = replace_item_table(source(""), "服务名称 | 服务范围\n虚构甲服务 | 软件运维\n服务设备 | 其他范围")
    if vertical:
        before, marker, rest = text.partition("--- HTML表格1 ---")
        text = before.replace(" | ", "\n") + marker + rest
    assert consume(monkeypatch, text, "软件运维服务", name="虚构甲服务").category == ""


def test_table_header_cannot_complete_preceding_attachment_text(monkeypatch):
    text = source("", attachment="软件运维\n服务名称 | 服务范围\n其他名称 | 其他范围")
    assert consume(monkeypatch, text, "软件运维服务").category == ""


@pytest.mark.parametrize("tail", ["品目名称:\n甲服务范围:乙服务", "所属行业:\n甲服务范围:乙服务",
                                  "未知说明:甲服务范围:乙服务"])
def test_next_line_declared_value_and_unknown_narrative_remain_opaque(monkeypatch, tail):
    text = source("", attachment=tail)
    assert consume(monkeypatch, text, "甲服务范围:乙服务").category == "甲服务范围:乙服务"


@pytest.mark.parametrize("category,tail", [
    ("服务/其他服务", "品目 | 服务/其他\n服务"),
    ("服务/其他服务", "名称 | 品目名称\n虚构 | 服务/其他\n服务"),
    ("C17010200 网络接入服务", "品目编号 | 品目名称\nC17010200 | 网络接入\n服务"),
    ("服务/其他服务", "品目 | 服务/其他\n | 服务"),
    ("服务/其他服务", "名称 | 品目名称\n虚构 | 服务/其他\n | 服务"),
    ("C17010200 网络接入服务", "品目编号 | 品目名称\nC17010200 | 网络接入\n | 服务"),
    ("服务/其他服务", "名称 | 品目名称 | 备注\n虚构 | 服务/其他 |\n | 服务 |"),
    ("服务/其他服务", "| 名称 | 品目名称 |\n| 虚构 | 服务/其他 |\n| | 服务 |"),
    ("服务/其他服务", "品目名称 | 名称\n服务/其他 |\n服务 |"),
    ("C17010200 网络接入服务", "品目编号 | 品目名称 | 备注\nC17010200 | 网络接入 |\n | 服务 |"),
])
def test_declared_category_cell_soft_wrap_is_not_a_cross_row_hit(monkeypatch, category, tail):
    assert consume(monkeypatch, source("", attachment=tail), category).category == category


@pytest.mark.parametrize("tail", ["未知说明 | 软件运维\n服务", "未知甲 | 未知乙\n说明 | 软件运维\n服务"])
def test_undeclared_pipe_narrative_softwrap_is_not_proof_of_a_false_category(monkeypatch, tail):
    assert consume(monkeypatch, source("", attachment=tail), "软件运维服务").category == "软件运维服务"


def test_helper_preserves_first_category_column_with_other_nonempty_cells():
    from extractor.base_category_roles import correct_base_category_roles
    from models.schemas import ExtractedEntity
    # The old global guard does not reconstruct this layout. This test targets
    # only the new helper, which must not introduce another deletion.
    text = source("", attachment="品目名称 | 名称\n服务/其他 | 虚构\n服务 |")
    original = ExtractedEntity(product_name="虚构平台维护服务", category="服务/其他服务")
    assert correct_base_category_roles([original], source_text=text)[0].category == "服务/其他服务"


@pytest.mark.parametrize("category,tail", [
    ("软件运维服务", "品目:软件运维服务"),
    ("服务/其他服务", "品目:服 务/其 他\n服 务"),
    ("C17010200 网络接入服务", "品目编号 | 品目名称\nC17010200 | 网络接入服务"),
    ("软件和信息技术服务业", "所属行业:软件和信息技术\n服务业"),
    ("货物类", "货物类主要标的信息:"),
])
def test_real_value_soft_wrap_declared_code_name_and_disputed_roles_remain(monkeypatch, category, tail):
    text = source(category="", attachment=tail)
    assert consume(monkeypatch, text, category).category == category


def test_details_keep_existing_source_guard_and_metadata(monkeypatch):
    row = consume(monkeypatch, source(), mode="details")
    assert row.category == "平台维护服务"
    assert (row.package_name, row.source_file, row.source_page) == ("包甲", "说明.txt", "7")


@pytest.mark.parametrize("where", ["metadata", "row", "industry", "table"])
def test_explicit_category_value_containing_a_field_like_label_is_not_destroyed(monkeypatch, where):
    value = "甲服务范围:乙服务"
    tail = "名称 | 品目名称\n虚构平台维护服务 | " + value if where == "table" else ("品目名称:" if where == "row" else "所属行业:") + value
    text = source(value) if where == "metadata" else source("", attachment=tail)
    assert consume(monkeypatch, text, value).category == value


def test_helper_preserves_original_type_metadata_other_fields_and_input():
    from extractor.base_category_roles import correct_base_category_roles
    from models.schemas import DetailedEntity
    original = DetailedEntity(product_name="虚构平台维护服务", category="平台维护服务", brand="虚构供应商",
                              package_name="包甲", source_file="来源", source_page="8")
    before = original.model_dump()
    result = correct_base_category_roles([original], source_text=source())[0]
    assert type(result) is DetailedEntity and result is not original
    assert original.model_dump() == before
    assert result.model_dump() == {**before, "category": "服务/其他服务"}


def test_helper_does_not_clear_absent_values_based_on_an_incomplete_cache():
    from extractor.base_category_roles import correct_base_category_roles
    from models.schemas import ExtractedEntity
    original = ExtractedEntity(product_name="虚构平台维护服务", category="C0201 软件开发服务")
    assert correct_base_category_roles([original], source_text=source(""))[0].category == "C0201 软件开发服务"


def test_correction_does_not_trigger_a_second_dedup(monkeypatch):
    extractor = object.__new__(LLMExtractor)
    rows = [{"product_name": "虚构平台维护服务", "category": c, "brand": "虚构供应商"}
            for c in ("平台维护服务", "服务/其他服务")]
    monkeypatch.setattr(extractor, "_call_llm", lambda *args: json.dumps({"entities": rows}, ensure_ascii=False))
    result = extractor.extract(source())
    assert result.success and len(result.entities) == 2
    assert [row.category for row in result.entities] == ["服务/其他服务", "服务/其他服务"]


def test_base_resume_invalidates_new_category_role_version(tmp_path, monkeypatch):
    import pipeline as module
    html = _write_announcement(tmp_path, "fictional-category-role-legacy")
    runner = _pipeline(tmp_path, _RecordingExtractor())
    first = runner.process_single_file(str(html), run_mode="legacy")
    monkeypatch.setattr(module, "BASE_CATEGORY_ROLE_RULE_VERSION", "test-next", raising=False)
    second = runner.process_single_file(str(html), run_mode="legacy", previous_result=first, resume=True)
    assert first.summary_input_hash != second.summary_input_hash


def test_base_version_does_not_invalidate_unchanged_details(tmp_path, monkeypatch):
    import pipeline as module
    html = write_html(tmp_path, "fictional-category-role-both", "成交金额100元")
    write_attachment_zip(html, "测试设备 | 100元 | 1台")
    runner = make_pipeline(tmp_path, RecordingExtractor())
    first = runner.process_single_file(str(html), run_mode="both")
    monkeypatch.setattr(module, "BASE_CATEGORY_ROLE_RULE_VERSION", "test-next", raising=False)
    second = runner.process_single_file(str(html), run_mode="both", previous_result=first, resume=True)
    assert first.summary_input_hash != second.summary_input_hash
    assert first.detail_input_hash == second.detail_input_hash
