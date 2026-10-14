"""V12 item arithmetic is independent of package awards; never copy the latter.

All fixtures are fictional. These former package-copy positives now assert the
literal item price 10元 × 2台 = 20元. Ownership/source conflict guards remain.
"""

import json
import pytest

from extractor.llm_extractor import LLMExtractor
from tests.test_legacy_base_layer import _pipeline, _RecordingExtractor, _write_announcement
from tests.test_dual_layer_pipeline import make_pipeline, RecordingExtractor, write_html, write_attachment_zip


def source(*, amount="20.0000（万元）", label="第4包 虚构屏幕：甲供应商", rows=None, extra_award="", attachment=""):
    header = "序号 | 供应商名称 | 货物名称 | 货物数量 | 货物单价（元）"
    rows = rows if rows is not None else [label + " | 虚构屏幕 | 2台 | 10"]
    item = header + "\n" + "\n".join(str(i) + " | " + row for i, row in enumerate(rows, 1))
    return ("二、项目名称：虚构项目\n三、中标（成交）信息\n供应商名称：" + label
            + "\n供应商地址：虚构地址\n中标（成交）金额：" + amount + "\n" + extra_award
            + "\n四、主要标的信息\n" + item + "\n五、评审专家\n"
            "--- HTML表格1 ---\n公告信息：\n采购项目名称 | 虚构项目\n"
            "--- HTML表格2 ---\n" + item
            + ("\n--- 附件：说明.txt ---\n" + attachment if attachment else ""))


def consume(monkeypatch, text, *, total="", name="虚构屏幕", brand="甲供应商", mode="legacy"):
    extractor = object.__new__(LLMExtractor)
    item = {"product_name": name, "category": "", "brand": brand, "spec_model": "X-1",
            "unit_price": "10元", "quantity": "2台", "total_price": total,
            "package_name": "采购包4", "source_page": "2"}
    response = {"detail_entities" if mode == "details" else "entities": [item]}
    monkeypatch.setattr(extractor, "_call_llm", lambda *args: json.dumps(response, ensure_ascii=False))
    if mode == "details":
        rows, status, error = extractor.extract_details(texts={"说明.txt": text}, tables={})
        assert status == "available", error
        return rows[0]
    result = extractor.extract(text) if mode == "legacy" else extractor.extract_summary(
        text, has_tables=True, detail_status="available")
    assert result.success, result.error_message
    return result.entities[0]


@pytest.mark.parametrize("mode", ["legacy", "summary"])
def test_empty_total_uses_proved_item_price_not_single_item_package_award(monkeypatch, mode):
    row = consume(monkeypatch, source(), mode=mode)
    assert row.model_dump() == {"product_name": "虚构屏幕", "category": "", "brand": "甲供应商",
                                "spec_model": "X-1", "unit_price": "10元", "quantity": "2台",
                                "total_price": "20元"}


@pytest.mark.parametrize("amount", ["81.9000000（万元）", "1,234.00元", "￥99.80万元（人民币）"])
def test_other_package_amount_spellings_never_replace_the_item_subtotal(monkeypatch, amount):
    from extractor.official_qa_money import award_records
    from extractor.supplier_rules import _scope
    assert consume(monkeypatch, source(amount=amount)).total_price == "20元"
    assert award_records(_scope(source(amount=amount))[0])[0]["amount"] == amount


@pytest.mark.parametrize("total", ["原金额", "10元", "未公示", "无", "详见附件", "78%"])
def test_unsupported_model_total_is_corrected_only_by_proved_item_money(monkeypatch, total):
    assert consume(monkeypatch, source(), total=total).total_price == "20元"


@pytest.mark.parametrize("amount", ["", "无", "未公示", "78%", "100元/年", "100万元/年", "100", "1 0万元",
                                    "1,00元", "100元及税费", "100-200元", "-10元"])
def test_bad_award_amount_is_never_the_item_total(monkeypatch, amount):
    # Empty declaration defers; percentage context also vetoes a generic
    # website 单价(元) header over a bare numeric price.
    expected = "" if amount in {"", "78%"} else "20元"
    assert consume(monkeypatch, source(amount=amount)).total_price == expected


@pytest.mark.parametrize("label", ["甲供应商", "第5包 虚构屏幕：甲供应商", "第4包 虚构屏幕：乙供应商"])
def test_missing_package_or_award_identity_mismatch_defers(monkeypatch, label):
    text = source()
    before, marker, rest = text.partition("四、主要标的信息")
    before = before.replace("第4包 虚构屏幕：甲供应商", label)
    expected = "20元" if label == "甲供应商" else ""
    assert consume(monkeypatch, before + marker + rest).total_price == expected


def test_same_supplier_award_for_another_package_cannot_be_borrowed(monkeypatch):
    text = source(extra_award="供应商名称：第5包 别的设备：甲供应商\n中标（成交）金额：50万元")
    assert consume(monkeypatch, text).total_price == "20元"


def test_multiple_products_in_one_package_cannot_each_receive_the_package_total(monkeypatch):
    rows = ["第4包 虚构屏幕：甲供应商 | 虚构屏幕 | 2台 | 10",
            "第4包 虚构屏幕：甲供应商 | 另一设备 | 1台 | 30"]
    assert consume(monkeypatch, source(rows=rows)).total_price == "20元"


def test_semicolon_joined_products_are_not_one_single_item(monkeypatch):
    rows = ["第4包 虚构屏幕：甲供应商 | 虚构屏幕；另一设备 | 2台；1台 | 10；30"]
    assert consume(monkeypatch, source(rows=rows), name="虚构屏幕；另一设备").total_price == ""


@pytest.mark.parametrize("amount", ["50万元", "", "20.0000（万元）"])
def test_multiple_same_package_award_blocks_are_not_unique(monkeypatch, amount):
    extra = "供应商名称：第4包 虚构屏幕：乙供应商\n中标（成交）金额：" + amount
    assert consume(monkeypatch, source(extra_award=extra)).total_price == ""


def test_two_amount_declarations_in_one_award_block_defers(monkeypatch):
    text = source(extra_award="中标（成交）金额：50万元")
    assert consume(monkeypatch, text).total_price == ""


@pytest.mark.parametrize("replacement", ["预算金额", "最高限价", "评审报价", "投标报价", "总中标金额"])
def test_other_amount_roles_are_not_explicit_package_awards(monkeypatch, replacement):
    expected = "20元" if replacement == "总中标金额" else ""
    assert consume(monkeypatch, source().replace("中标（成交）金额：", replacement + "：")).total_price == expected


def test_award_section_role_cannot_be_replaced_by_bid_ranking(monkeypatch):
    assert consume(monkeypatch, source().replace("三、中标（成交）信息", "三、投标报价排名")).total_price == ""


def test_attachment_award_cannot_replace_absent_html_award(monkeypatch):
    text = source(amount="", attachment="供应商名称：第4包 虚构屏幕：甲供应商\n中标（成交）金额：20万元")
    assert consume(monkeypatch, text).total_price == ""


def test_conflicting_body_and_metadata_project_defers(monkeypatch):
    assert consume(monkeypatch, source().replace("二、项目名称：虚构项目", "二、项目名称：另一项目")).total_price == ""


def test_detached_serialized_item_is_not_an_owner(monkeypatch):
    text = source().replace("| 虚构屏幕 | 2台 | 10\n五、", "| 别的设备 | 2台 | 10\n五、")
    assert consume(monkeypatch, text).total_price == ""


def test_existing_brand_mismatch_is_not_overwritten_to_make_an_award_fit(monkeypatch):
    assert consume(monkeypatch, source(), brand="乙供应商").total_price == ""


def test_details_do_not_receive_base_empty_total_fill(monkeypatch):
    row = consume(monkeypatch, source(), mode="details")
    assert row.total_price == ""
    assert (row.package_name, row.source_file, row.source_page) == ("采购包4", "说明.txt", "2")


def test_award_header_currency_does_not_override_item_currency(monkeypatch):
    text = source(amount="20").replace("中标（成交）金额：", "中标（成交）金额（万元）：")
    assert consume(monkeypatch, text).total_price == "20元"


def test_conflicting_award_currency_is_not_used_for_item_arithmetic(monkeypatch):
    text = source(amount="20万元").replace("中标（成交）金额：", "中标（成交）金额（元）：")
    assert consume(monkeypatch, text).total_price == "20元"


def test_duplicate_package_identity_inside_supplier_label_defers(monkeypatch):
    assert consume(monkeypatch, source(label="第4包 第5包 虚构屏幕：甲供应商")).total_price == ""


def test_other_item_with_unknown_package_is_not_allocated_to_this_item(monkeypatch):
    rows = ["第4包 虚构屏幕：甲供应商 | 虚构屏幕 | 2台 | 10", "甲供应商 | 另一设备 | 1台 | 20"]
    assert consume(monkeypatch, source(rows=rows)).total_price == "20元"


def test_standalone_competing_supplier_without_money_still_preserves_conflict(monkeypatch):
    extra = "供应商名称\n第4包 虚构屏幕：乙供应商\n供应商地址：别处"
    assert consume(monkeypatch, source(extra_award=extra)).total_price == ""


@pytest.mark.parametrize("extra", ["中标（成交）金额\n50万元",
                                  "供应商名称 | 中标（成交）金额\n第4包 虚构屏幕：乙供应商 | 50万元",
                                  "成交供应商：第4包 虚构屏幕：乙供应商",
                                  "中标供应商：第4包 虚构屏幕：乙供应商",
                                  "成交金额（单位：万元）：50"])
def test_unparsed_explicit_award_conflict_cannot_be_ignored(monkeypatch, extra):
    assert consume(monkeypatch, source(extra_award=extra)).total_price == ""


@pytest.mark.parametrize("separator", ["、", "，", ","])
def test_obvious_product_lists_are_not_a_single_item(monkeypatch, separator):
    names = "虚构屏幕" + separator + "另一设备"
    rows = ["第4包 虚构屏幕：甲供应商 | " + names + " | 2台" + separator + "1台 | 10" + separator + "30"]
    assert consume(monkeypatch, source(rows=rows), name=names).total_price == ""


@pytest.mark.parametrize("location", ["before", "after"])
def test_additional_different_item_is_not_used_to_claim_a_single_item_package(monkeypatch, location):
    extra = "第4包\n货物名称：另一设备\n货物数量：1台\n"
    text = source().replace("四、主要标的信息\n", "四、主要标的信息\n" + extra) if location == "before" else source().replace("五、评审专家", extra + "五、评审专家")
    assert consume(monkeypatch, text).total_price == "20元"


def test_award_section_package_context_conflicting_with_supplier_label_defers(monkeypatch):
    text = source().replace("三、中标（成交）信息\n", "三、中标（成交）信息\n采购包5\n")
    assert consume(monkeypatch, text).total_price == ""


@pytest.mark.parametrize("where", ["body", "serialized"])
def test_incomplete_main_item_row_does_not_prove_single_item_package(monkeypatch, where):
    partial = "\n2 | 第4包 虚构屏幕：甲供应商 | 另一设备 | 1台"
    text = source()
    text = text.replace("\n五、评审专家", partial + "\n五、评审专家") if where == "body" else text + partial
    assert consume(monkeypatch, text).total_price == ""


@pytest.mark.parametrize("brand,expected", [("制造商甲", "20元"), ("甲供应商", ""), ("制造商乙", "")])
def test_manufacturer_brand_is_distinct_from_the_award_supplier(monkeypatch, brand, expected):
    text = source().replace("货物名称 | 货物数量", "货物名称 | 货物品牌 | 货物数量")
    text = text.replace("虚构屏幕 | 2台", "虚构屏幕 | 制造商甲 | 2台")
    assert consume(monkeypatch, text, brand=brand).total_price == expected


@pytest.mark.parametrize("brand", ["详见附件", "未公示", "无"])
def test_missing_brand_is_not_invented_but_unique_actual_item_can_have_price(monkeypatch, brand):
    text = source().replace("货物名称 | 货物数量", "货物名称 | 货物品牌 | 货物数量")
    text = text.replace("虚构屏幕 | 2台", "虚构屏幕 | " + brand + " | 2台")
    row = consume(monkeypatch, text, brand=brand)
    assert row.brand == "" and row.total_price == "20元"


def test_multiple_manufacturer_brand_columns_defers(monkeypatch):
    text = source().replace("货物名称 | 货物数量", "货物名称 | 货物品牌 | 品牌 | 货物数量")
    text = text.replace("虚构屏幕 | 2台", "虚构屏幕 | 制造商甲 | 制造商甲 | 2台")
    assert consume(monkeypatch, text, brand="制造商甲").total_price == ""


def test_matching_explicit_award_context_is_not_a_conflict(monkeypatch):
    text = source().replace("三、中标（成交）信息\n", "三、中标（成交）信息\n采购包4\n")
    assert consume(monkeypatch, text).total_price == "20元"


def test_helper_preserves_type_metadata_original_objects_and_other_six_fields():
    from extractor.total_award_rules import fill_empty_award_totals
    from models.schemas import DetailedEntity
    original = DetailedEntity(product_name="虚构屏幕", brand="甲供应商", total_price="",
                              package_name="采购包4", source_file="原文件", source_page="9")
    row = fill_empty_award_totals([original], source_text=source())[0]
    assert type(row) is DetailedEntity and row is not original
    assert original.total_price == ""
    assert row.model_dump() == {"product_name": "虚构屏幕", "category": "", "brand": "甲供应商",
                               "spec_model": "", "unit_price": "", "quantity": "", "total_price": "",
                               "package_name": "采购包4", "source_file": "原文件", "source_page": "9"}


def test_fill_does_not_trigger_a_second_dedup(monkeypatch):
    extractor = object.__new__(LLMExtractor)
    rows = [{"product_name": "虚构屏幕", "brand": "甲供应商", "total_price": value}
            for value in ("", "20.0000（万元）")]
    monkeypatch.setattr(extractor, "_call_llm", lambda *args: json.dumps({"entities": rows}, ensure_ascii=False))
    result = extractor.extract(source())
    assert result.success and len(result.entities) == 2
    assert [row.total_price for row in result.entities] == ["", ""]  # original multiplicity remains ambiguous


def test_legacy_resume_invalidates_new_award_total_version(tmp_path, monkeypatch):
    import pipeline as module
    html = _write_announcement(tmp_path, "fictional-total-legacy")
    runner = _pipeline(tmp_path, _RecordingExtractor())
    first = runner.process_single_file(str(html), run_mode="legacy")
    monkeypatch.setattr(module, "TOTAL_AWARD_RULE_VERSION", "test-total-next", raising=False)
    second = runner.process_single_file(str(html), run_mode="legacy", previous_result=first, resume=True)
    assert first.summary_input_hash != second.summary_input_hash


def test_summary_resume_change_does_not_invalidate_details(tmp_path, monkeypatch):
    import pipeline as module
    html = write_html(tmp_path, "fictional-total-both", "测试设备，成交金额100元")
    write_attachment_zip(html, "测试设备 | 100元 | 1台")
    runner = make_pipeline(tmp_path, RecordingExtractor())
    first = runner.process_single_file(str(html), run_mode="both")
    monkeypatch.setattr(module, "TOTAL_AWARD_RULE_VERSION", "test-total-next", raising=False)
    second = runner.process_single_file(str(html), run_mode="both", previous_result=first, resume=True)
    assert first.summary_input_hash != second.summary_input_hash
    assert first.detail_input_hash == second.detail_input_hash
