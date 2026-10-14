"""Official QA regression: item money is not award money; demand is not award."""
from copy import deepcopy
import importlib

import pytest

from data_loader.html_parser import HTMLAnnouncementParser
from extractor.summary_rules import build_summary_source, sanitize_summary_entities
from models.schemas import ExtractedEntity, DetailedEntity, ExtractionResult


def content_with_rows(rows, *, headers=None, award_rows=None, tail="", item_heading="四、主要标的信息："):
    headers = headers or ["序号", "供应商名称", "货物名称", "货物品牌", "货物型号", "货物数量", "货物单价(元)"]
    award_rows = award_rows or [("第3包-甲公司", "150万元")]
    results = "".join(f"<p>供应商名称：{owner}</p><p>中标（成交）金额：{price}</p>" for owner, price in award_rows)
    table = "<table><tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
    table += "".join("<tr>" + "".join(f"<td>{v}</td>" for v in row) + "</tr>" for row in rows) + "</table>"
    html = ("<h2>虚构测试项目中标公告</h2><div class='vF_detail_content'><table>"
            "<tr><th>公告信息：</th></tr><tr><td>采购项目名称</td><td>虚构测试项目</td></tr></table>"
            "<p>二、项目名称：虚构测试项目</p><p>三、中标（成交）信息：</p>" + results +
            f"<p>{item_heading}</p>" + table + "<p>五、评审专家名单：</p>" + tail + "</div>")
    return HTMLAnnouncementParser().parse_html(html, "synthetic")


def clean(entities, content):
    return sanitize_summary_entities(entities, detail_status="available", source_text=build_summary_source(content.html_text, content.html_tables))


def policy():
    spec = importlib.util.find_spec("extractor.official_qa")
    assert spec is not None, "official QA policy not implemented"
    return importlib.import_module("extractor.official_qa")


def test_product_subtotal_replaces_copied_package_amount():
    source = content_with_rows([["3", "第3包-甲公司", "审计设备", "品牌甲", "X-1", "3", "250,000.00"]])
    original = ExtractedEntity(product_name="审计设备", brand="品牌甲", spec_model="X-1", quantity="3", unit_price="250,000.00", total_price="150万元")
    saved = original.model_copy(deep=True)
    result = clean([original], source)
    assert result[0].total_price == "750000元"
    assert result[0].quantity == "3" and original == saved


def test_empty_product_subtotal_can_be_safely_derived():
    source = content_with_rows([["3", "第3包-甲公司", "审计设备", "品牌甲", "X-1", "3", "250,000.00"]])
    result = clean([ExtractedEntity(product_name="审计设备", brand="品牌甲", spec_model="X-1")], source)
    assert result[0].total_price == "750000元"


def test_explicit_product_total_precedes_multiplication():
    source = content_with_rows([["3", "第3包-甲公司", "审计设备", "品牌甲", "X-1", "3", "250000", "760000"]],
                               headers=["序号", "供应商名称", "货物名称", "货物品牌", "货物型号", "货物数量", "货物单价(元)", "总价(元)"])
    result = clean([ExtractedEntity(product_name="审计设备", brand="品牌甲", spec_model="X-1", total_price="150万元")], source)
    assert result[0].total_price == "760000元"


@pytest.mark.parametrize("qty,price", [("", "250000"), ("详见附件", "250000"), ("3", "78%"), ("3", "800元/人/天"), ("服务期3年", "250000")])
def test_no_guessing_or_residual_when_item_price_not_usable(qty, price):
    source = content_with_rows([["3", "第3包-甲公司", "审计设备", "品牌甲", "X-1", qty, price]])
    result = clean([ExtractedEntity(product_name="审计设备", brand="品牌甲", spec_model="X-1", total_price="150万元")], source)
    assert result[0].total_price == ""  # copied package amount cannot stand in for the product


def test_same_item_in_two_packages_is_not_matched_by_amount():
    source = content_with_rows([["1", "第1包-甲公司", "审计设备", "品牌甲", "X-1", "3", "250000"],
                               ["2", "第2包-乙公司", "审计设备", "品牌甲", "X-1", "2", "100000"]],
                               award_rows=[("第1包-甲公司", "150万元"), ("第2包-乙公司", "20万元")])
    original = ExtractedEntity(product_name="审计设备", brand="品牌甲", spec_model="X-1", total_price="150万元")
    result = clean([original], source)
    assert result[0].total_price == ""


def test_award_rows_without_products_preserve_duplicates_under_master_project():
    rows = [["甲公司", "", "", "1", "28.1万元", "28.1万元", "详见附件"],
            ["甲公司", "", "", "1", "28.1万元", "28.1万元", "详见附件"],
            ["乙公司", "", "", "1", "43.2万元", "43.2万元", "详见附件"]]
    source = content_with_rows(rows, headers=["供应商名称", "服务名称", "服务范围", "数量", "单价", "总价", "服务标准"],
                               award_rows=[("甲公司", "28.1万元"), ("甲公司", "28.1万元"), ("乙公司", "43.2万元")],
                               tail="<table><tr><td>包号</td><td>标的名称</td></tr><tr><td>03</td><td>计划服务</td></tr></table>")
    guessed = [ExtractedEntity(product_name="计划服务", brand="甲公司", total_price="28.1万元")]
    result = clean(guessed, source)
    assert [(r.product_name, r.brand, r.total_price) for r in result] == [
        ("虚构测试项目", "甲公司", "28.1万元"), ("虚构测试项目", "甲公司", "28.1万元"), ("虚构测试项目", "乙公司", "43.2万元")]
    assert all(r.unit_price == r.quantity == r.spec_model == "" for r in result)


def test_plan_rows_go_to_notes_without_becoming_awarded_products():
    source = "采购需求\n采购预算\n产品名称 | 数量 | 预算单价(元)\n审计设备 | 3 | 100\nAPI工具 | 2 | 200"
    original = ExtractionResult(announcement_id="synthetic", detail_entities=[DetailedEntity(product_name="API工具", source_file="采购.pdf", source_page="4", total_price="400元")])
    saved = deepcopy(original)
    result = policy().apply_official_qa(original, content_with_rows([]), attachment_sources={"采购.pdf": source})
    assert result.detail_entities == []
    notes = result.extra_info["official_qa"]["excluded_details"]
    assert notes[0]["classification"] == "planned_requirement"
    assert notes[0]["entity"]["product_name"] == "API工具" and original == saved


def test_mixed_attachment_does_not_exclude_award_table_by_filename():
    source = ("采购需求\n产品名称 | 数量 | 预算价\n计划设备 | 2 | 100\n"
              "中标明细表\n产品名称 | 数量 | 单价(元)\n成交设备 | 3 | 50")
    original = ExtractionResult(announcement_id="synthetic", detail_entities=[
        DetailedEntity(product_name="计划设备", source_file="招标文件.pdf"),
        DetailedEntity(product_name="成交设备", source_file="招标文件.pdf", quantity="3", unit_price="50元")])
    result = policy().apply_official_qa(original, content_with_rows([]), attachment_sources={"招标文件.pdf": source})
    assert [r.product_name for r in result.detail_entities] == ["成交设备"]
    assert result.detail_entities[0].total_price == "150元"


def test_filename_alone_and_unread_sources_never_certify_detail_role():
    original = ExtractionResult(announcement_id="synthetic", detail_entities=[DetailedEntity(product_name="设备", source_file="招标文件.pdf")])
    result = policy().apply_official_qa(original, content_with_rows([]), attachment_sources={})
    assert len(result.detail_entities) == 1
    assert result.extra_info["official_qa"]["unverified_detail_count"] == 1


def test_explicit_nonaward_package_is_excluded_but_not_inferred_from_absence():
    source = content_with_rows([["1", "第1包-甲公司", "成交设备", "品牌甲", "X-1", "1", "100"]],
                               tail="<p>第2包废标</p>")
    original = ExtractionResult(announcement_id="synthetic", entities=[ExtractedEntity(product_name="计划设备（第2包）", brand="乙公司")],
                                detail_entities=[DetailedEntity(product_name="计划设备", package_name="第2包", source_file="未知.pdf")])
    result = policy().apply_official_qa(original, source, attachment_sources={})
    assert result.entities == [] and result.detail_entities == []
    assert result.extra_info["official_qa"]["excluded_details"][0]["classification"] == "nonaward_package"


def test_policy_idempotence_and_extra_info_preserved():
    original = ExtractionResult(announcement_id="synthetic", extra_info={"project_amount": "150万元", "private_old_note": "retained"})
    content = content_with_rows([])
    once = policy().apply_official_qa(original, content, attachment_sources={})
    twice = policy().apply_official_qa(once, content, attachment_sources={})
    assert once == twice and twice.extra_info["project_amount"] == "150万元"


@pytest.mark.parametrize("heading", ["本表不是中标明细表，是采购需求", "中标明细表（投标模板）", "中标一览表格式范本"])
def test_template_or_negated_award_heading_never_certifies_actual_purchase(heading):
    source = "采购需求\n" + heading + "\n产品名称 | 数量 | 单价(元)\n计划设备 | 2 | 100"
    original = ExtractionResult(announcement_id="synthetic", detail_entities=[DetailedEntity(product_name="计划设备", source_file="任意.pdf")])
    result = policy().apply_official_qa(original, content_with_rows([]), attachment_sources={"任意.pdf": source})
    assert result.detail_entities == []
    assert result.extra_info["official_qa"]["excluded_details"][0]["classification"] == "planned_requirement"


def test_detail_price_does_not_choose_between_same_product_in_two_packages():
    source = ("中标明细表\n包号 | 产品名称 | 数量 | 单价(元)\n"
              "第1包 | 成交设备 | 2 | 100\n第2包 | 成交设备 | 3 | 200")
    original = ExtractionResult(announcement_id="synthetic", detail_entities=[DetailedEntity(product_name="成交设备", source_file="明细.pdf", total_price="")])
    result = policy().apply_official_qa(original, content_with_rows([]), attachment_sources={"明细.pdf": source})
    assert result.detail_entities[0].total_price == ""


def test_verified_page_role_does_not_leak_to_other_pages():
    original = ExtractionResult(announcement_id="synthetic", detail_entities=[DetailedEntity(product_name="计划设备", source_file="任意.pdf", source_page="9")])
    source = {"任意.pdf": {"pages": {"4": "采购需求\n产品名称 | 数量 | 预算价\n计划设备 | 2 | 100"}}}
    result = policy().apply_official_qa(original, content_with_rows([]), attachment_sources=source)
    assert len(result.detail_entities) == 1 and result.extra_info["official_qa"]["unverified_detail_count"] == 1


def test_existing_persistence_dedup_retains_only_source_proved_master_award_multiplicity():
    from extractor.cross_package_duplicates import deduplicate_with_source_evidence
    rows = [["甲公司", "", "", "1", "28.1万元", "28.1万元"], ["甲公司", "", "", "1", "28.1万元", "28.1万元"],
            ["乙公司", "", "", "1", "43.2万元", "43.2万元"]]
    content = content_with_rows(rows, headers=["供应商名称", "服务名称", "服务范围", "数量", "单价", "总价"],
                                award_rows=[("甲公司", "28.1万元"), ("甲公司", "28.1万元"), ("乙公司", "43.2万元")])
    source = build_summary_source(content.html_text, content.html_tables)
    records = clean([], content)
    assert len(records) == 3
    assert len(deduplicate_with_source_evidence(records, source_text=source)) == 3
    assert len(deduplicate_with_source_evidence([*records, records[0]], source_text=source)) == 2
    assert len(deduplicate_with_source_evidence(records, source_text="")) == 2


def test_goods_header_inside_same_html_table_is_a_separate_owned_matrix():
    source = content_with_rows([
        ["1", "第1包-甲公司", "维护服务", "范围", "要求", "一年", "标准"],
        ["序号", "供应商名称", "货物名称", "货物品牌", "货物型号", "货物数量", "货物单价(元)"],
        ["3", "第3包-乙公司", "审计设备", "品牌甲", "X-1", "3", "250000"]],
        headers=["序号", "供应商名称", "服务名称", "服务范围", "服务要求", "服务时间", "服务标准"],
        award_rows=[("第1包-甲公司", "60万元"), ("第3包-乙公司", "150万元")])
    result = clean([ExtractedEntity(product_name="审计设备", brand="品牌甲", spec_model="X-1", total_price="150万元")], source)
    assert result[0].total_price == "750000元"


def test_supplier_and_commodity_name_header_aliases_support_master_award_records():
    rows = [["甲公司", "", "", "1", "28.1万元", "28.1万元"], ["乙公司", "", "", "1", "43.2万元", "43.2万元"]]
    source = content_with_rows(rows, headers=["供应商", "商品名称", "规格型号", "数量", "单价", "总价"],
                               award_rows=[("甲公司", "28.1万元"), ("乙公司", "43.2万元")])
    result = clean([ExtractedEntity(product_name="猜测的需求项目", brand="甲公司", total_price="28.1万元")], source)
    assert [(r.product_name, r.brand, r.total_price) for r in result] == [("虚构测试项目", "甲公司", "28.1万元"), ("虚构测试项目", "乙公司", "43.2万元")]


def test_item_calculation_keeps_the_distinct_disclosed_package_award_in_notes():
    content = content_with_rows([["3", "第3包-甲公司", "审计设备", "品牌甲", "X-1", "3", "250000"]])
    original = ExtractionResult(announcement_id="synthetic", entities=[ExtractedEntity(
        product_name="审计设备", brand="品牌甲", spec_model="X-1", total_price="150万元")],
        extra_info={"project_amount": "150万元"})
    once = policy().apply_official_qa(original, content)
    notes = once.extra_info["official_qa"]
    assert once.entities[0].total_price == "750000元"
    assert notes["disclosed_awards"][0]["amount"] == "150万元"
    assert notes["disclosed_awards"][0]["package"] == "包:3"
    proof = notes["money_evidence"][0]
    assert proof["scope"] == "item" and proof["method"] == "unit_times_quantity"
    assert (proof["unit_price"], proof["quantity"], proof["after"]) == ("250000", "3", "750000元")
    assert len(proof["source_text_sha256"]) == 64
    assert once == policy().apply_official_qa(once, content)


def test_explicit_item_total_evidence_is_labeled_not_claimed_as_a_calculation():
    content = content_with_rows([["3", "第3包-甲公司", "审计设备", "品牌甲", "X-1", "3", "250000", "760000"]],
        headers=["序号", "供应商名称", "货物名称", "货物品牌", "货物型号", "货物数量", "货物单价(元)", "总价(元)"])
    result = policy().apply_official_qa(ExtractionResult(announcement_id="synthetic", entities=[ExtractedEntity(
        product_name="审计设备", brand="品牌甲", spec_model="X-1", total_price="150万元")]), content)
    proof = result.extra_info["official_qa"]["money_evidence"][0]
    assert proof["scope"] == "item" and proof["method"] == "explicit_item_total"
    assert proof["after"] == "760000元"


@pytest.mark.parametrize("changes", [{"brand": "其他品牌"}, {"spec_model": "X-2"}, {"package_name": "第2包"}])
def test_detail_money_rejects_contradictory_item_identity(changes):
    text = "中标明细表\n包号 | 产品名称 | 品牌 | 规格型号 | 数量 | 单价(元)\n第1包 | 成交设备 | 品牌甲 | X-1 | 3 | 50"
    detail = DetailedEntity(**({"product_name": "成交设备", "source_file": "明细.pdf", "brand": "品牌甲", "spec_model": "X-1", "package_name": "第1包"} | changes))
    result = policy().apply_official_qa(ExtractionResult(announcement_id="synthetic", detail_entities=[detail]),
                                     content_with_rows([]), attachment_sources={"明细.pdf": text})
    assert result.detail_entities[0].total_price == ""


def test_unsupported_explicit_detail_total_cannot_be_bypassed_by_arithmetic():
    text = "中标明细表\n产品名称 | 数量 | 单价(元) | 总价\n成交设备 | 3 | 50 | 78%"
    detail = DetailedEntity(product_name="成交设备", source_file="明细.pdf")
    result = policy().apply_official_qa(ExtractionResult(announcement_id="synthetic", detail_entities=[detail]),
                                     content_with_rows([]), attachment_sources={"明细.pdf": text})
    assert result.detail_entities[0].total_price == ""


@pytest.mark.parametrize("nested", [False, True])
def test_percentage_award_vetoes_unitless_item_price_despite_generic_yuan_header(nested):
    content = content_with_rows([["3", "第3包-甲公司", "食品", "品牌甲", "X-1", "1批", "6.5"]],
                                award_rows=[("第3包-甲公司", "6.5（%）")])
    if nested:
        content.html_text = content.html_text.replace("供应商名称：", "1.中标结果：\n供应商名称：", 1)
    result = clean([ExtractedEntity(product_name="食品", brand="品牌甲", spec_model="X-1")], content)
    assert result[0].total_price == ""


@pytest.mark.parametrize("location", ["main_items", "supplement", "fragmented_supplement"])
def test_another_named_actual_award_table_vetoes_empty_product_master_fallback(location):
    content = content_with_rows([["甲公司", "", "", "1", "0万元", "0万元"],
                                 ["乙公司", "", "", "1", "1229615元", "1229615元"]],
        headers=["供应商", "商品名称", "规格型号", "数量", "单价", "总价"],
        award_rows=[("甲公司", "0万元"), ("乙公司", "1229615元")])
    actual = [["包号", "中标单位名称", "服务内容", "中标单位地址", "中标金额"],
              ["1", "甲公司", "井盖调试服务", "地址甲", "450元/处"],
              ["2", "乙公司", "账号采购", "地址乙", "1229615元"]]
    content.html_tables.append(actual)
    matrix = "\n".join(" | ".join(r) for r in actual)
    if location == "main_items":
        content.html_text = content.html_text.replace("五、评审专家名单：", matrix + "\n五、评审专家名单：")
    else:
        if location == "fragmented_supplement":
            matrix = "\n".join(c for r in actual for c in r).replace("450元/处", "450\n元\n/\n处")
        content.html_text += "\n八、其他补充事宜：\n" + matrix
    rows = [ExtractedEntity(product_name="井盖调试服务", brand="甲公司", unit_price="450元/处"),
            ExtractedEntity(product_name="账号采购", brand="乙公司", total_price="1229615元")]
    result = policy().apply_official_qa(ExtractionResult(announcement_id="synthetic", entities=rows), content)
    assert [r.product_name for r in result.entities] == ["井盖调试服务", "账号采购"]
    assert result.entities[0].unit_price == "450元/处"


@pytest.mark.parametrize("header", ["货物数量(万台)", "货物数量(年)", "货物数量(人天)"])
def test_scaled_or_duration_quantity_header_cannot_certify_base_arithmetic(header):
    content = content_with_rows([["3", "第3包-甲公司", "设备", "品牌甲", "X-1", "3", "100"]],
        headers=["序号", "供应商名称", "货物名称", "货物品牌", "货物型号", header, "货物单价(元)"])
    result = clean([ExtractedEntity(product_name="设备", brand="品牌甲", spec_model="X-1")], content)
    assert result[0].total_price == ""


def test_detail_scaled_quantity_header_is_not_ignored():
    text = "中标明细表\n产品名称 | 数量(万台) | 单价(元)\n设备 | 3 | 100"
    original = ExtractionResult(announcement_id="synthetic", detail_entities=[DetailedEntity(product_name="设备", source_file="明细.pdf")])
    result = policy().apply_official_qa(original, content_with_rows([]), attachment_sources={"明细.pdf": text})
    assert result.detail_entities[0].total_price == ""


def test_two_conflicting_detail_total_columns_veto_arithmetic():
    text = "中标明细表\n产品名称 | 数量 | 单价(元) | 总价(元) | 金额(元)\n设备 | 3 | 100 | 280 | 290"
    original = ExtractionResult(announcement_id="synthetic", detail_entities=[DetailedEntity(product_name="设备", source_file="明细.pdf")])
    result = policy().apply_official_qa(original, content_with_rows([]), attachment_sources={"明细.pdf": text})
    assert result.detail_entities[0].total_price == ""


def test_vertical_plan_table_product_name_is_also_classified_as_demand():
    text = "采购需求\n产品名称\n设备\n数量\n3\n预算单价(元)\n100"
    original = ExtractionResult(announcement_id="synthetic", detail_entities=[DetailedEntity(product_name="设备", source_file="需求.pdf")])
    result = policy().apply_official_qa(original, content_with_rows([]), attachment_sources={"需求.pdf": text})
    assert result.detail_entities == []
    assert result.extra_info["official_qa"]["excluded_details"][0]["classification"] == "planned_requirement"


def test_explicit_package_context_is_not_dropped_to_force_master_fallback():
    content = content_with_rows([["甲公司", "", "", "1", "28.1万元", "28.1万元"],
                                 ["乙公司", "", "", "1", "43.2万元", "43.2万元"]],
        headers=["供应商", "商品名称", "规格型号", "数量", "单价", "总价"],
        award_rows=[("甲公司", "28.1万元"), ("乙公司", "43.2万元")])
    content.html_text = content.html_text.replace("供应商名称：甲公司", "第1包\n供应商名称：甲公司").replace("供应商名称：乙公司", "第2包\n供应商名称：乙公司")
    rows = [ExtractedEntity(product_name="项目（第1包）", brand="甲公司", total_price="28.1万元"),
            ExtractedEntity(product_name="项目（第2包）", brand="乙公司", total_price="43.2万元")]
    result = policy().apply_official_qa(ExtractionResult(announcement_id="synthetic", entities=rows), content)
    assert result.entities == rows
    assert [a["package"] for a in result.extra_info["official_qa"]["disclosed_awards"]] == ["包:1", "包:2"]


def test_unowned_scope_list_does_not_invent_product_to_award_mapping():
    content = content_with_rows([["甲公司", "", "", "1", "28.1万元", "28.1万元"],
                                 ["乙公司", "", "", "1", "43.2万元", "43.2万元"]],
        headers=["供应商", "商品名称", "规格型号", "数量", "单价", "总价"],
        award_rows=[("甲公司", "28.1万元"), ("乙公司", "43.2万元")])
    scope = [["包号", "标的名称", "项目用途", "简要技术要求"], ["01", "甲服务", "计划用途", "要求"], ["02", "乙服务", "计划用途", "要求"]]
    content.html_tables.append(scope)
    content.html_text = content.html_text.replace("五、评审专家名单：", "\n".join(" | ".join(r) for r in scope) + "\n五、评审专家名单：")
    rows = [ExtractedEntity(product_name="甲服务", brand="甲公司", total_price="28.1万元")]
    result = policy().apply_official_qa(ExtractionResult(announcement_id="synthetic", entities=rows), content)
    assert len(result.entities) == 2
    assert all(r.product_name == "虚构测试项目" for r in result.entities)
