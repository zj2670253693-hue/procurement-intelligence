"""Only restore a uniquely sourced quantity header unit; do not repair numbers."""

import json
import pytest

from extractor.llm_extractor import LLMExtractor
from tests.test_legacy_base_layer import _pipeline, _RecordingExtractor, _write_announcement
from tests.test_dual_layer_pipeline import make_pipeline, RecordingExtractor, write_html, write_attachment_zip


def source(attachment="", *, html_quantity=None, project="虚构项目", supplier="第1包-甲供应商"):
    header = "供应商名称 | 服务名称" + (" | 数量" if html_quantity is not None else "")
    row = supplier + " | 虚构服务" + (" | " + html_quantity if html_quantity is not None else "")
    return ("二、项目名称：" + project + "\n四、主要标的信息\n" + header + "\n" + row
            + "\n五、评审专家\n--- HTML表格1 ---\n公告信息：\n采购项目名称 | " + project
            + "\n品目 | 服务/其他服务\n--- HTML表格2 ---\n" + header + "\n" + row
            + ("\n--- 附件：采购文件.pdf ---\n" + attachment if attachment else ""))


def table(unit="台/套", number="1", name="虚构服务", package="1", tail=""):
    return ("项目名称：虚构项目\n包号 | 标的名称 | 数量（" + unit + "） | 预算单价（元） | 预算金额（元）\n"
            + package + " | " + name + " | " + number + " | 100 | 100\n" + tail)


def wrapped_table(name="虚构服务", package="1", number="1", tail=""):
    return ("项目名称：虚构项目\n包 数量 预算单价 预算金额 是否允许进口\n标的名称\n"
            "号 （台/套） （元） （元） 产品投标\n" + name[:2] + "\n"
            + package + " " + number + " 100.00 100.00 否\n" + name[2:] + "\n" + tail)


def consume(monkeypatch, text, *, quantity="1", mode="legacy", brand="甲供应商"):
    extractor = object.__new__(LLMExtractor)
    row = {"product_name": "虚构服务", "category": "服务/其他服务", "brand": brand,
           "spec_model": "X-1", "unit_price": "100元", "quantity": quantity, "total_price": "100元",
           "package_name": "采购包1", "source_page": "2"}
    response = {"detail_entities" if mode == "details" else "entities": [row]}
    monkeypatch.setattr(extractor, "_call_llm", lambda *args: json.dumps(response, ensure_ascii=False))
    if mode == "details":
        details, status, error = extractor.extract_details(texts={"明细.txt": text}, tables={})
        assert status == "available", error
        return details[0]
    result = (extractor.extract(text) if mode == "legacy" else extractor.extract_summary(
        text, has_tables=True, detail_status="available"))
    assert result.success, result.error_message
    return result.entities[0]


@pytest.mark.parametrize("quantity", ["1", "1.0000"])
def test_same_item_package_and_quantity_header_restores_only_unit(monkeypatch, quantity):
    row = consume(monkeypatch, source(table()), quantity=quantity)
    assert row.model_dump() == {"product_name": "虚构服务", "category": "服务/其他服务", "brand": "甲供应商",
                                "spec_model": "X-1", "unit_price": "100元", "quantity": quantity + "（台/套）",
                                "total_price": "100元"}


def test_complete_wrapped_quantity_row_can_prove_name_without_using_budget_prices(monkeypatch):
    assert consume(monkeypatch, source(wrapped_table())).quantity == "1（台/套）"


def test_inline_name_quantity_row_in_wrapped_header_is_supported(monkeypatch):
    attachment = ("项目名称：虚构项目\n包 数量 预算单价 预算金额\n标的名称\n"
                  "号 (台/套) （元） （元）\n1 虚构服务 1 100.00 100.00\n注：采购需求详见第五章")
    assert consume(monkeypatch, source(attachment)).quantity == "1（台/套）"


@pytest.mark.parametrize("quantity", ["", "1项", "1（台/套）", "3年", "1338人", "1-2", "78%", "1,00", "-1"])
def test_existing_units_missing_or_non_scalar_quantities_are_not_changed(monkeypatch, quantity):
    assert consume(monkeypatch, source(table()), quantity=quantity).quantity == quantity


@pytest.mark.parametrize("html_quantity", ["1", "3", "1项"])
@pytest.mark.parametrize("mode", ["legacy", "summary"])
def test_html_declared_quantity_is_not_overridden_or_augmented_from_attachment(monkeypatch, mode, html_quantity):
    assert consume(monkeypatch, source(table(), html_quantity=html_quantity), mode=mode).quantity == "1"


@pytest.mark.parametrize("attachment", [
    table(number="2"), table(number="详见附件"), table(unit="元"), table(unit="元/台"),
    table(name="另一服务"), table(package="2"),
    table(tail="1 | 虚构服务 | 2 | 100 | 100"),
    table() + "\n包号 | 标的名称 | 数量（项） | 预算单价（元） | 预算金额（元）\n1 | 虚构服务 | 1 | 100 | 100",
    table().replace("项目名称：虚构项目", "项目名称：其他项目"),
    "报价表模板\n" + table(),
    table().replace("数量（台/套）", "服务范围（台/套）"),
    table().replace("数量（台/套）", "服务时间（年）"),
    table().replace("数量（台/套）", "预算数量无单位"),
    table().replace(" | 1 | 100 | 100", " | 1 | 100"),
    wrapped_table(name="另一服务"), wrapped_table(package="2"),
    wrapped_table().replace("号 （台/套） （元） （元） 产品投标", "号 （台） （套） （元） 产品投标"),
])
def test_ambiguous_scope_unit_role_number_row_or_package_evidence_defers(monkeypatch, attachment):
    assert consume(monkeypatch, source(attachment)).quantity == "1"


def test_body_metadata_project_conflict_defers(monkeypatch):
    text = source(table()).replace("二、项目名称：虚构项目", "二、项目名称：其他项目")
    assert consume(monkeypatch, text).quantity == "1"


def test_unique_name_still_rejects_conflicting_supplier_identity(monkeypatch):
    assert consume(monkeypatch, source(table()), brand="乙供应商").quantity == "1"


def test_summary_receives_html_only_and_cannot_read_missing_attachment(monkeypatch):
    assert consume(monkeypatch, source(), mode="summary").quantity == "1"


def test_details_do_not_inherit_base_quantity_unit_rule(monkeypatch):
    row = consume(monkeypatch, source(table()), mode="details")
    assert row.quantity == "1"
    assert (row.package_name, row.source_file, row.source_page) == ("采购包1", "明细.txt", "2")


def test_main_item_table_cannot_be_replaced_by_bid_ranking_section(monkeypatch):
    text = source(table()).replace("四、主要标的信息", "四、投标报价排名")
    assert consume(monkeypatch, text).quantity == "1"


def test_detached_html_table_not_proven_in_main_item_body_defers(monkeypatch):
    text = source(table()).replace("第1包-甲供应商 | 虚构服务\n五、评审专家",
                                   "第1包-甲供应商 | 另一服务\n五、评审专家")
    assert consume(monkeypatch, text).quantity == "1"


def test_clause_mentioning_template_is_not_itself_a_template_table(monkeypatch):
    attachment = table() + "\n投标人应按投标文件格式准备其他资料。"
    assert consume(monkeypatch, source(attachment)).quantity == "1（台/套）"


def test_only_table_in_template_chapter_is_not_a_quantity_source(monkeypatch):
    assert consume(monkeypatch, source("第七章 投标文件格式\n" + table())).quantity == "1"


def test_later_template_chapter_does_not_erase_earlier_real_quantity_or_add_conflict(monkeypatch):
    attachment = table() + "\n第七章 投标文件格式\n" + table(unit="项")
    assert consume(monkeypatch, source(attachment)).quantity == "1（台/套）"


def test_attachment_standalone_conflicting_project_identity_defers(monkeypatch):
    assert consume(monkeypatch, source("采购项目名称\n另一个项目\n" + table())).quantity == "1"


def test_wrapped_project_name_requires_exact_reconstruction(monkeypatch):
    attachment = table().replace("项目名称：虚构项目", "项目名称：虚构\n项目")
    assert consume(monkeypatch, source(attachment)).quantity == "1（台/套）"


def test_wrapped_project_prefix_cannot_hide_a_different_identity(monkeypatch):
    attachment = table().replace("项目名称：虚构项目", "项目名称：虚构\n项目二")
    assert consume(monkeypatch, source(attachment)).quantity == "1"


def test_quantity_row_cannot_borrow_an_earlier_header_across_prose_boundary(monkeypatch):
    attachment = table().replace("1 | 虚构服务", "下面为与本表无关的条款：\n1 | 虚构服务")
    assert consume(monkeypatch, source(attachment)).quantity == "1"


def test_serialized_html_declared_quantity_cannot_be_hidden_by_body_placeholder(monkeypatch):
    text = source(table(), html_quantity="详见附件")
    before, marker, serialized = text.partition("--- HTML表格2 ---")
    text = before + marker + serialized.replace(" | 详见附件", " | 1项", 1)
    assert consume(monkeypatch, text).quantity == "1"


def test_serialized_same_package_identity_conflict_retains_quantity_negative_evidence(monkeypatch):
    text = source(table(), html_quantity="详见附件")
    before, marker, serialized = text.partition("--- HTML表格2 ---")
    serialized = serialized.replace("第1包-甲供应商", "第1包-乙供应商", 1).replace(" | 详见附件", " | 1项", 1)
    assert consume(monkeypatch, before + marker + serialized).quantity == "1"


def test_space_separated_number_tokens_are_not_a_single_bare_number(monkeypatch):
    assert consume(monkeypatch, source(table(number="10")), quantity="1 0").quantity == "1 0"


def test_space_separated_source_number_tokens_cannot_prove_numeric_equality(monkeypatch):
    assert consume(monkeypatch, source(table(number="1 0")), quantity="10").quantity == "10"


def test_missing_package_is_not_same_package_evidence(monkeypatch):
    text = source(table().replace("包号 |", "序号 |"), supplier="甲供应商")
    assert consume(monkeypatch, text).quantity == "1"


def test_duplicate_package_columns_cannot_be_hidden_by_name_package(monkeypatch):
    attachment = table(name="第1包虚构服务").replace("包号 |", "包号 | 标包 |").replace("1 | 第1包", "2 | B | 第1包")
    text = source(attachment).replace(" | 虚构服务", " | 第1包虚构服务")
    extractor = object.__new__(LLMExtractor)
    monkeypatch.setattr(extractor, "_call_llm", lambda *args: json.dumps({"entities": [{
        "product_name": "第1包虚构服务", "brand": "甲供应商", "quantity": "1"}]}, ensure_ascii=False))
    assert extractor.extract(text).entities[0].quantity == "1"


@pytest.mark.parametrize("heading", ["投标文件格式：", "投标文件格式 |", "| 投标文件格式 |", "（模板）投标文件格式",
                                     "投标文件格式（模板）", "投标文件格式 | 说明"])
def test_punctuated_template_heading_is_not_an_actual_quantity_table(monkeypatch, heading):
    assert consume(monkeypatch, source(heading + "\n" + table())).quantity == "1"


@pytest.mark.parametrize("heading", ["第七章 投标文件格式", "投标文件格式："])
def test_template_scope_survives_numbered_subsection(monkeypatch, heading):
    assert consume(monkeypatch, source(heading + "\n1、报价清单\n" + table())).quantity == "1"


def test_new_non_template_chapter_can_reestablish_actual_source_region(monkeypatch):
    attachment = "第七章 投标文件格式\n1、报价清单\n" + table(unit="项")
    attachment += "\n第八章 实际采购需求\n" + table()
    assert consume(monkeypatch, source(attachment)).quantity == "1（台/套）"


def test_mixed_body_headers_preserve_explicit_primary_quantity_role():
    from extractor.quantity_units import quantity_unit_decisions
    from models.schemas import ExtractedEntity
    service_header = "供应商名称 | 服务名称 | 服务范围"
    goods_header = "供应商名称 | 货物名称 | 货物数量"
    text = ("四、主要标的信息\n" + service_header + "\n第1包-甲供应商 | 另一服务 | 详见附件\n"
            + goods_header + "\n第2包-甲供应商 | 虚构服务 | 1\n五、评审专家\n"
            "--- HTML表格1 ---\n公告信息：\n采购项目名称 | 虚构项目\n"
            "--- HTML表格2 ---\n" + service_header + "\n第1包-甲供应商 | 另一服务 | 详见附件\n"
            + goods_header + "\n第2包-甲供应商 | 虚构服务 | 1\n"
            "--- 附件：采购文件.pdf ---\n" + table(package="2"))
    assert quantity_unit_decisions([ExtractedEntity(product_name="虚构服务", brand="甲供应商", quantity="1")],
                                   source_text=text) == [("", "preserve_html_declared_quantity")]


def test_two_wrapped_names_cannot_be_assigned_to_rows_by_appearance_order(monkeypatch):
    attachment = ("项目名称：虚构项目\n包 数量 预算单价 预算金额\n标的名称\n"
                  "号 (台/套) （元） （元）\n第一产品前缀\n1 1 100.00 100.00\n"
                  "第一产品后缀\n第二产品前缀\n2 1 100.00 100.00\n虚构服务")
    assert consume(monkeypatch, source(attachment, supplier="第2包-甲供应商")).quantity == "1"


def test_source_conflicting_body_package_cannot_be_hidden_by_supplier_prefix(monkeypatch):
    text = source(table()).replace("四、主要标的信息\n", "四、主要标的信息\n第2包：\n")
    assert consume(monkeypatch, text).quantity == "1"


def test_helper_copies_type_source_metadata_and_input_without_other_field_changes():
    from extractor.quantity_units import restore_quantity_units
    from models.schemas import DetailedEntity
    original = DetailedEntity(product_name="虚构服务", brand="甲供应商", quantity="1",
                              package_name="采购包1", source_file="证据.txt", source_page="2")
    copied = restore_quantity_units([original], source_text=source(table()))[0]
    assert type(copied) is DetailedEntity and copied is not original
    assert original.quantity == "1"
    assert copied.model_dump() == {"product_name": "虚构服务", "category": "", "brand": "甲供应商",
                                   "spec_model": "", "unit_price": "", "quantity": "1（台/套）", "total_price": "",
                                   "package_name": "采购包1", "source_file": "证据.txt", "source_page": "2"}


def test_restoration_does_not_trigger_second_dedup(monkeypatch):
    extractor = object.__new__(LLMExtractor)
    rows = [{"product_name": "虚构服务", "brand": "甲供应商", "quantity": q} for q in ("1", "1（台/套）")]
    monkeypatch.setattr(extractor, "_call_llm", lambda *args: json.dumps({"entities": rows}, ensure_ascii=False))
    result = extractor.extract(source(table()))
    assert result.success, result.error_message
    assert len(result.entities) == 2
    assert [r.quantity for r in result.entities] == ["1（台/套）", "1（台/套）"]


def test_legacy_resume_invalidates_quantity_unit_version(tmp_path, monkeypatch):
    import pipeline as module
    html = _write_announcement(tmp_path, "fictional-quantity-legacy")
    pipeline = _pipeline(tmp_path, _RecordingExtractor())
    first = pipeline.process_single_file(str(html), run_mode="legacy")
    monkeypatch.setattr(module, "QUANTITY_UNIT_RULE_VERSION", "test-quantity-next", raising=False)
    second = pipeline.process_single_file(str(html), run_mode="legacy", previous_result=first, resume=True)
    assert first.summary_input_hash != second.summary_input_hash


def test_summary_resume_change_leaves_detail_fingerprint_unchanged(tmp_path, monkeypatch):
    import pipeline as module
    html = write_html(tmp_path, "fictional-quantity-both", "测试设备，成交金额100元")
    write_attachment_zip(html, "测试设备 | 100元 | 1台")
    pipeline = make_pipeline(tmp_path, RecordingExtractor())
    first = pipeline.process_single_file(str(html), run_mode="both")
    monkeypatch.setattr(module, "QUANTITY_UNIT_RULE_VERSION", "test-quantity-next", raising=False)
    second = pipeline.process_single_file(str(html), run_mode="both", previous_result=first, resume=True)
    assert first.summary_input_hash != second.summary_input_hash
    assert first.detail_input_hash == second.detail_input_hash
