import copy
import importlib
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))


def tool():
    assert importlib.util.find_spec("standard_export") is not None, "standard export not implemented"
    return importlib.import_module("standard_export")


def result():
    return {"announcement_id": "t20260101_12345678", "entities": [
        {"product_name": "设备甲", "brand": "品牌甲", "spec_model": "X1", "quantity": "1套",
         "unit_price": "125.50元", "total_price": "125.50元", "category": "设备"}],
        "detail_entities": [{"product_name": "计划采购乙", "package_name": "二包"}],
        "extra_info": {"official_qa": {"unverified_detail_count": 1}}}


def source():
    return {"project_name": "演示采购项目", "procurement_unit": "演示单位", "divided": False,
            "packages": [], "classification": "award_candidate", "awards": [], "bidders": [],
            "item_bindings": [], "source_sha256": "demo", "warnings": []}


def test_money_item_precision_project_rounding_and_rate_not_money():
    m = tool()
    assert m.money_wan("125.50元") == "0.012550"
    assert m.money_wan("1234567元", round_two=True) == "123.46"
    assert m.money_wan("0.009万元") == "0.009"
    assert m.money_wan("0元") == "0.0000"
    for text in ("78%", "6万元×78%", "100元/人天", "详见附件", "100", "NaN"):
        assert m.money_wan(text) == ""


def test_undivided_preserves_id_units_seven_fields_and_raw_notes():
    m = tool(); raw = result(); before = copy.deepcopy(raw)
    bundle = m.project([raw], {raw["announcement_id"]: source()})
    row = bundle["tables"]["标的表"][0]
    assert row["UUID"] == raw["announcement_id"] and row["标段/合同包"] == "未分包"
    assert row["数量"] == "1套" and row["单价"] == "0.012550" and row["总价"] == "0.012550"
    assert "official_qa" not in row
    assert raw == before
    assert bundle["advanced"][0]["raw_result"] == before
    assert len(bundle["tables"]["标的表"]) == 1  # Unverified planned attachment is not promoted.


def test_shared_uuid_is_stable_on_input_permutation_not_actual_package_number():
    m = tool(); raw = result(); raw["entities"] = [dict(raw["entities"][0], product_name="甲包设备"),
                                                 dict(raw["entities"][0], product_name="乙包设备")]
    s = source(); s.update(divided=True, packages=[{"key": "p2", "label": "第二包"}, {"key": "p7", "label": "第七包"}],
                          awards=[{"package_key": "p7", "supplier": "乙公司", "amount": "200元"},
                                  {"package_key": "p2", "supplier": "甲公司", "amount": "100元"}],
                          bidders=[{"package_key": "p7", "name": "乙公司", "category": "中标人"}],
                          item_bindings=[{"product_name": "甲包设备", "package_key": "p2"},
                                         {"product_name": "乙包设备", "package_key": "p7"}])
    first = m.project([raw], {raw["announcement_id"]: s})
    s["packages"].reverse(); s["awards"].reverse(); raw["entities"].reverse()
    second = m.project([raw], {raw["announcement_id"]: s})
    assert first["uuid_map"] == second["uuid_map"]
    ids = {r["标段/合同包"]: r["UUID"] for r in first["tables"]["总表"]}
    assert ids["第二包"] == "t20260101_12345678_01"
    assert ids["第七包"] == "t20260101_12345678_02"
    assert all(r["UUID"] == ids[r["标段/合同包"]] for r in first["tables"]["标的表"])
    assert first["tables"]["投标人表"][0]["UUID"] == ids["第七包"]


def test_unknown_mapping_is_not_undivided_or_guessed_from_order():
    m = tool(); raw = result(); s = source()
    s.update(divided=True, packages=[{"key": "p1", "label": "包1"}, {"key": "p2", "label": "包2"}],
             awards=[{"supplier": "甲公司", "amount": "10万元"}, {"supplier": "乙公司", "amount": "20万元"}])
    bundle = m.project([raw], {raw["announcement_id"]: s})
    assert all(r["标段/合同包"] == "" for r in bundle["tables"]["总表"] + bundle["tables"]["标的表"])
    assert all(r["UUID"] == raw["announcement_id"] for r in bundle["tables"]["总表"])
    assert "对应关系未知" in bundle["tables"]["标的表"][0]["备注"]


def test_intention_is_excluded_from_three_standard_views_but_preserved():
    m = tool(); raw = result(); s = source(); s["classification"] = "intention_only"
    bundle = m.project([raw], {raw["announcement_id"]: s})
    assert all(not rows for rows in bundle["tables"].values())
    assert bundle["advanced"][0]["classification"] == "intention_only"
    assert bundle["advanced"][0]["raw_result"] == raw
    assert bundle["audit"][0]["excluded_base_rows"] == 1


def test_no_inferred_totals_and_bidder_review_not_assumed_from_award():
    m = tool(); raw = result(); raw["entities"][0]["total_price"] = ""
    s = source(); s["bidders"] = [{"name": "甲公司", "category": "中标人", "quote": "", "review": ""}]
    bundle = m.project([raw], {raw["announcement_id"]: s})
    assert bundle["tables"]["标的表"][0]["总价"] == ""
    bidder = bundle["tables"]["投标人表"][0]
    assert bidder["通过审查"] == "" and bidder["报价（万元）"] == ""


def test_identical_owner_amount_occurrences_are_not_deduplicated():
    m = tool(); raw = result(); s = source()
    s["awards"] = [{"supplier": "甲公司", "amount": "28.1万元"}] * 2
    assert len(m.project([raw], {raw["announcement_id"]: s})["tables"]["总表"]) == 2


def test_duplicate_or_missing_source_ids_fail_closed():
    m = tool(); raw = result()
    with pytest.raises(ValueError): m.project([raw, raw], {raw["announcement_id"]: source()})
    with pytest.raises(ValueError): m.project([raw], {})


def test_title_only_framework_does_not_remove_actual_second_stage_transaction():
    m = tool()
    assert m.classify("框架协议第二阶段成交公告", "实际成交数量2台，成交金额200元") == "award_candidate"
    assert m.classify("采购入围结果公告", "入围供应商名称 入围单价（元） 数量：/") == "intention_only"
    assert m.classify("普通中标公告", "供应商名称甲公司 成交金额100元 附：框架协议制度") == "award_candidate"
    assert m.classify("框架协议采购公告", "本次仅征集供应商，尚无实际成交") == "intention_only"


def test_source_projection_uses_only_explicit_bidder_cells_and_actual_unit_headers():
    m = tool()
    html = """<h2>演示项目中标公告</h2><div class='vF_detail_content'>
    <table><tr><th>公告信息：</th></tr><tr><td>采购项目名称</td><td>演示项目</td></tr>
    <tr><td>采购单位</td><td>演示单位</td></tr></table>
    <p>三、中标（成交）信息：</p><p>供应商名称：甲公司</p><p>中标金额：125.50元</p>
    <p>四、主要标的信息：</p><table><tr><th>货物名称</th><th>品牌</th><th>规格型号</th><th>数量</th><th>单价(元)</th><th>总价(元)</th></tr>
    <tr><td>设备甲</td><td>品牌甲</td><td>X1</td><td>1套</td><td>125.50</td><td>125.50</td></tr></table>
    <p>评审情况：</p><table><tr><th>供应商名称</th><th>资格审查</th><th>报价(元)</th><th>综合得分</th></tr>
    <tr><td>甲公司</td><td>通过</td><td>125.50</td><td>90.20</td></tr>
    <tr><td>乙公司</td><td>未通过：资质不符合</td><td>130.00</td><td></td></tr></table></div>"""
    s = m.parse_source(html.encode(), "synthetic")
    assert s["project_name"] == "演示项目" and s["procurement_unit"] == "演示单位"
    assert s["awards"][0]["supplier"] == "甲公司"
    a, b = s["bidders"]
    assert a["category"] == "中标人" and a["review"] == "通过" and a["quote_unit"] == "元"
    assert b["category"] == "其他" and b["review"] == "未通过：资质不符合"
    assert a["score"] == "90.20"


def test_csv_quotes_names_and_does_not_put_formula_strings_in_numeric_cells():
    m = tool()
    data = m.csv_bytes("投标人表", [{"序号": 1, "UUID": "abc", "名称": "甲,乙\n公司", "报价（万元）": ""}])
    import csv, io
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    assert rows[0]["名称"] == "甲,乙\n公司" and rows[0]["报价（万元）"] == ""


def test_source_hash_failure_prevents_creating_any_candidate_output(tmp_path):
    m = tool(); raw = tmp_path / "raw.json"; raw.write_bytes(b"[]")
    with pytest.raises(ValueError, match="raw hash"):
        m.run_candidate(tmp_path / "out", raw_path=raw)
    assert not (tmp_path / "out").exists()


def test_real_shortlist_and_two_actual_goods_guards():
    m = tool()
    stage = Path(__file__).resolve().parent.parent / "task1-release-validation-20261012-v12/stage-final-r2/backend/sample_data"
    expected = {"t20260508_26523610": "intention_only", "t20260702_26855564": "award_candidate",
                "t20260401_26345150": "award_candidate", "t20260206_26158480": "award_candidate"}
    for aid, category in expected.items():
        assert m.parse_source((stage / (aid + ".html")).read_bytes(), aid)["classification"] == category


def test_parenthesized_unit_keeps_explicit_award_and_numeric_zero():
    m = tool()
    assert m.money_wan("243.1310000（万元）", round_two=True) == "243.13"
    assert m.money_wan("125.50（元）") == "0.012550"
    assert m.money_wan(0, declared_unit="元") == "0.0000"


def test_missing_narrow_package_token_does_not_claim_undivided():
    m = tool()
    stage = Path(__file__).resolve().parent.parent / "task1-release-validation-20261012-v12/stage-final-r2/backend/sample_data"
    s = m.parse_source((stage / "t20260202_26139731.html").read_bytes(), "t20260202_26139731")
    assert s["divided"] is not False
    raw = result(); raw["announcement_id"] = "t20260202_26139731"
    rows = m.project([raw], {raw["announcement_id"]: s})["tables"]["总表"]
    assert all(r["标段/合同包"] != "未分包" for r in rows)


def test_bidder_winner_is_package_scoped_and_same_supplier_other_award_not_dropped():
    m = tool()
    html = """<h2>两个包中标公告</h2><div class='vF_detail_content'>
    <table><tr><th>公告信息：</th></tr><tr><td>采购项目名称</td><td>项目甲</td></tr>
    <tr><td>采购单位</td><td>单位甲</td></tr></table>
    <p>三、中标（成交）信息：</p><p>第1包</p><p>供应商名称：甲公司</p><p>中标金额：100元</p>
    <p>第2包</p><p>供应商名称：乙公司</p><p>中标金额：200元</p>
    <p>四、主要标的信息：</p><p>详见附件</p>
    <table><tr><th>采购包</th><th>供应商名称</th><th>资格审查</th><th>报价(元)</th></tr>
    <tr><td>第2包</td><td>甲公司</td><td>通过</td><td>220</td></tr></table></div>"""
    s = m.parse_source(html.encode(), "synthetic")
    entries = {(b["name"], b["package_key"]): b["category"] for b in s["bidders"]}
    assert entries[("甲公司", "包:2")] == "参与人"
    assert entries[("甲公司", "包:1")] == "中标人"
    assert entries[("乙公司", "包:2")] == "中标人"


def test_offered_quantity_never_overrides_explicit_no_transaction_statement():
    m = tool()
    assert m.classify("框架协议第一阶段入围结果公告", "尚无实际成交；入围供应商提供设备1台 单价100元",
                      concrete_transaction=True) == "intention_only"


def test_invalid_award_and_bidder_money_is_audited_not_silently_zero():
    m = tool(); raw = result(); s = source()
    s["awards"] = [{"supplier": "甲公司", "amount": "市场指导价78%"}]
    s["bidders"] = [{"name": "甲公司", "quote": "95%", "category": "参与人"}]
    bundle = m.project([raw], {raw["announcement_id"]: s})
    assert bundle["tables"]["总表"][0]["中标金额"] == ""
    assert bundle["tables"]["投标人表"][0]["报价（万元）"] == ""
    fields = {r["field"] for r in bundle["audit"][0]["invalid_money_fields"]}
    assert {"中标金额", "报价（万元）"} <= fields


def test_explicit_supplier_cell_package_caption_maps_without_using_row_order():
    m = tool()
    aid = "t20260202_26139731"
    stage = Path(__file__).resolve().parent.parent / "task1-release-validation-20261012-v12/stage-final-r2/backend/sample_data"
    s = m.parse_source((stage / (aid + ".html")).read_bytes(), aid)
    award_keys = {r["supplier"]: r["package_key"] for r in s["awards"]}
    assert award_keys["北京万迪科信息技术有限公司"] == "包:3"
    assert award_keys["北京智为视媒科技有限公司"] == "包:4"


def test_csv_external_text_cannot_become_a_formula_when_opened():
    m = tool()
    import csv, io
    data = m.csv_bytes("投标人表", [{"名称": "=CMD()", "UUID": "synthetic", "序号": 1}])
    row = next(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))
    assert row["名称"] == "'=CMD()"


def test_combined_item_names_can_share_explicit_package_without_price_pairing():
    m = tool()
    aid = "t20260202_26139731"
    stage = Path(__file__).resolve().parent.parent / "task1-release-validation-20261012-v12/stage-final-r2/backend/sample_data"
    s = m.parse_source((stage / (aid + ".html")).read_bytes(), aid)
    bindings = {b["product_name"]: b["package_key"] for b in s["item_bindings"]}
    assert bindings["光跳纤架"] == "包:3" and bindings["光熔纤柜"] == "包:3"


def test_real_complete_result_matrix_and_header_unit_are_promoted():
    m = tool()
    stage = Path(__file__).resolve().parent.parent / "task1-release-validation-20261012-v12/stage-final-r2/backend/sample_data"
    s = m.parse_source((stage / "t20260202_26139917.html").read_bytes(), "t20260202_26139917")
    assert len(s["awards"]) == 1
    assert s["awards"][0]["supplier"] == "中国太平洋人寿保险股份有限公司绥化中心支公司"
    assert m.money_wan(s["awards"][0]["amount"], declared_unit=s["awards"][0].get("amount_unit", "")) == "122.226300"
    assert s["awards"][0]["package_key"] == "包:1"
    other = m.parse_source((stage / "t20260203_26146584.html").read_bytes(), "t20260203_26146584")
    assert len(other["awards"]) == 1
    assert m.money_wan(other["awards"][0]["amount"], declared_unit=other["awards"][0].get("amount_unit", ""), round_two=True) == "756.40"


def test_result_rows_preserve_occurrences_but_reject_rate_values():
    m = tool()
    stage = Path(__file__).resolve().parent.parent / "task1-release-validation-20261012-v12/stage-final-r2/backend/sample_data"
    s = m.parse_source((stage / "t20260204_26150301.html").read_bytes(), "t20260204_26150301")
    assert len(s["awards"]) == 3
    assert s["awards"][0]["supplier"] == s["awards"][1]["supplier"]
    assert all(not a["package_key"] for a in s["awards"])
    rates = m.parse_source((stage / "t20260203_26146674.html").read_bytes(), "t20260203_26146674")
    assert not rates["awards"]


def test_result_matrix_rejects_candidate_budget_spans_and_conflicting_currency():
    import importlib
    assert importlib.util.find_spec("source_awards") is not None, "structured result parser missing"
    parse = importlib.import_module("source_awards").parse_award_tables
    def html(section, header="中标金额(万元)", amount="100", span=""):
        return (f"<div class='vF_detail_content'><p>{section}</p><table><tr><th>供应商名称</th><th>{header}</th></tr>"
                f"<tr><td {span}>甲公司</td><td>{amount}</td></tr></table><p>四、主要标的信息</p></div>").encode()
    assert len(parse(html("三、采购结果"))["awards"]) == 1
    assert not parse(html("三、中标候选人信息"))["awards"]
    assert not parse(html("三、预算明细"))["awards"]
    assert not parse(html("三、采购结果", amount="100元"))["awards"]
    assert not parse(html("三、采购结果", span='rowspan="2"'))["awards"]


def test_bidder_aliases_combine_only_disclosed_review_and_do_not_borrow_currency():
    m = tool()
    stage = m.SOURCE_ROOT
    s = m.parse_source((stage / "t20260202_26139917.html").read_bytes(), "t20260202_26139917")
    bidders = [b for b in s["bidders"] if b.get("quote")]
    assert bidders
    assert bidders[0]["name"] == "中国太平洋人寿保险股份有限公司绥化中心支公司"
    assert bidders[0]["review"] == "通过"
    assert bidders[0]["quote"] == "1,222,263.00"
    assert bidders[0]["quote_unit"] == ""  # Award 元 does not assign the bidder table unit.


def test_two_review_columns_with_missing_second_review_do_not_imply_pass():
    m = tool()
    html = """<h2>项目中标公告</h2><div class='vF_detail_content'>
    <table><tr><th>公告信息：</th></tr><tr><td>采购项目名称</td><td>项目</td></tr><tr><td>采购单位</td><td>单位</td></tr></table>
    <p>三、中标信息：</p><p>供应商名称：甲公司</p><p>中标金额：100元</p><p>四、主要标的信息：</p>
    <table><tr><th>供应商</th><th>资格性审查</th><th>符合性审查</th><th>最终报价(元)</th></tr>
    <tr><td>甲公司</td><td>通过</td><td></td><td>100</td></tr></table></div>"""
    s = m.parse_source(html.encode(), "synthetic")
    bidder = next(b for b in s["bidders"] if b.get("quote"))
    assert bidder["review"] == "" and bidder["quote_unit"] == "元"


def test_matrix_missing_supplier_marker_cannot_become_an_amount_only_award():
    from source_awards import parse_award_tables
    for supplier in ("/", "无", "未公示", "详见附件"):
        html = ("<p>三、采购结果</p><table><tr><th>供应商名称</th><th>中标金额(元)</th></tr>"
                f"<tr><td>{supplier}</td><td>100</td></tr></table>").encode()
        parsed = parse_award_tables(html)
        assert not parsed["awards"]
        assert parsed["rejected"]


def test_compound_package_caption_or_cell_does_not_guess_first_package():
    from source_awards import parse_award_tables
    for caption, package_header, package_cell in (
        ("合同包1、合同包2", "", ""),
        ("合同包1", "<th>采购包</th>", "<td>合同包1、合同包2</td>"),
        ("合同包1", "<th>采购包</th>", "<td>未知</td>"),
    ):
        html = (f"<p>三、采购结果</p><p>{caption}</p><table><tr>{package_header}"
                f"<th>供应商名称</th><th>中标金额(元)</th></tr><tr>{package_cell}"
                "<td>甲公司</td><td>100</td></tr></table>").encode()
        parsed = parse_award_tables(html)
        assert len(parsed["awards"]) == 1
        assert parsed["awards"][0]["package_key"] == ""


def test_standard_package_column_alias_cannot_borrow_a_conflicting_caption():
    from source_awards import parse_award_tables
    def parsed(value):
        html = ("<p>三、采购结果</p><p>合同包1</p><table><tr><th>标段/合同包</th>"
                f"<th>供应商名称</th><th>中标金额(元)</th></tr><tr><td>{value}</td>"
                "<td>甲公司</td><td>100</td></tr></table>").encode()
        return parse_award_tables(html)
    conflict = parsed("合同包2")
    assert not conflict["awards"]
    assert conflict["rejected"][0]["reason"] == "conflicting_package"
    assert parsed("未知")["awards"][0]["package_key"] == ""
    assert parsed("合同包1")["awards"][0]["package_key"] == "包:1"
