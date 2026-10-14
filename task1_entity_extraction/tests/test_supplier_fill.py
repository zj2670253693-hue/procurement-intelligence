"""Only uniquely sourced base suppliers may replace empty/reference brands."""

import json

import pytest

from extractor.llm_extractor import LLMExtractor
from test_announcement_category_fill import metadata
from test_dual_layer_pipeline import RecordingExtractor, make_pipeline, write_attachment_zip, write_html
from test_legacy_base_layer import _RecordingExtractor, _pipeline, _write_announcement


def direct_source(rows="甲供应商 | 虚构服务", tail="", header="供应商名称 | 货物名称"):
    return ("四、主要标的信息\n成交金额：100元\n" + header + "\n" + rows + "\n五、评审专家\n" + metadata()
            + "\n--- HTML表格2 ---\n" + header + "\n" + rows + "\n" + tail)


def package_source(second_amount="200元", award_heading="三、中标信息", tail=""):
    return (f"{award_heading}\n标包：A\n供应商名称：甲供应商\n中标金额：100元\n"
            f"标包：B\n供应商名称：乙供应商\n中标金额：{second_amount}\n"
            "四、主要标的信息\n标包：A\n名称：虚构服务\n品牌：详见附件\n"
            "标包：B\n名称：虚构服务\n品牌：详见附件\n五、评审专家\n"
            + metadata() + "\n" + tail)


def column_source():
    return ("三、中标信息\n第1包：\n供应商名称\n中标金额(万元)\n甲供应商\n0.01\n"
            "第2包：\n供应商名称\n中标金额(万元)\n乙供应商\n0.02\n"
            "通过资格审查的供应商评审报价：\n第1包：\n丙投标人\n0.009\n"
            "四、主要标的信息\n第1包：\n类型\n名称\n服务类\n虚构服务\n"
            "第2包：\n类型\n名称\n服务类\n另一服务\n五、评审专家\n"
            + metadata()
            + "\n--- HTML表格2 ---\n供应商名称 | 中标金额(万元)\n甲供应商 | 0.01\n"
            "--- HTML表格3 ---\n供应商名称 | 中标金额(万元)\n乙供应商 | 0.02\n"
            "--- HTML表格4 ---\n排序 | 供应商名称 | 评审报价(万元)\n1 | 丙投标人 | 0.009\n"
            "--- HTML表格5 ---\n类型 | 名称\n服务类 | 虚构服务\n"
            "--- HTML表格6 ---\n类型 | 名称\n服务类 | 另一服务")


def consume(monkeypatch, mode, text, *, brands=("",), names=None, amounts=None):
    extractor = object.__new__(LLMExtractor)
    names = names or ["虚构服务"] * len(brands)
    amounts = amounts or ["100元"] * len(brands)
    rows = [{"product_name": name, "category": "服务/其他服务", "brand": brand,
             "spec_model": "X-1", "unit_price": "100元", "quantity": "1项", "total_price": amount,
             "package_name": "包A", "source_page": "3"}
            for name, brand, amount in zip(names, brands, amounts)]
    response = {"detail_entities" if mode == "details" else "entities": rows}
    # The only substitute is the external, billable model call.
    monkeypatch.setattr(extractor, "_call_llm", lambda *args: json.dumps(response, ensure_ascii=False))
    if mode == "legacy":
        result = extractor.extract(text, announcement_id="fictional-supplier")
    elif mode == "summary":
        result = extractor.extract_summary(text, has_tables=True, detail_status="available")
    else:
        details, status, error = extractor.extract_details(texts={"明细.txt": text}, tables={})
        assert status == "available", error
        return details
    assert result.success, result.error_message
    return result.entities


@pytest.mark.parametrize("mode", ["legacy", "summary"])
@pytest.mark.parametrize("brand", ["", "详见附件", "见附件", "详见招标文件", "详见招标文件。"])
def test_unique_same_item_row_fills_empty_or_pure_reference_brand(monkeypatch, mode, brand):
    rows = consume(monkeypatch, mode, direct_source(), brands=[brand])
    assert rows[0].model_dump() == {
        "product_name": "虚构服务", "category": "服务/其他服务", "brand": "甲供应商",
        "spec_model": "X-1", "unit_price": "100元", "quantity": "1项", "total_price": "100元",
    }


@pytest.mark.parametrize("mode", ["legacy", "summary"])
def test_same_name_packages_use_unique_explicit_award_amount_not_row_order(monkeypatch, mode):
    rows = consume(monkeypatch, mode, package_source(), brands=["", "详见附件"], amounts=["200元", "100元"])
    assert [r.brand for r in rows] == ["乙供应商", "甲供应商"]
    assert [r.total_price for r in rows] == ["200元", "100元"]


@pytest.mark.parametrize("mode", ["legacy", "summary"])
def test_column_award_table_links_through_explicit_package_item_not_bid_ranking(monkeypatch, mode):
    rows = consume(monkeypatch, mode, column_source())
    assert rows[0].brand == "甲供应商"


@pytest.mark.parametrize("mode", ["legacy", "summary"])
def test_inline_supplier_literal_preserves_original_parentheses(monkeypatch, mode):
    text = package_source().replace("甲供应商", "甲供应商（华北）")
    assert consume(monkeypatch, mode, text)[0].brand == "甲供应商（华北）"


@pytest.mark.parametrize("mode", ["legacy", "summary"])
@pytest.mark.parametrize("brand", ["定制化", "迈瑞", "无", "未公示", "详见附件中的某某品牌", "已有供应商"])
def test_nonempty_literal_brand_is_not_overwritten(monkeypatch, mode, brand):
    expected = "" if brand in {"无", "未公示"} else brand
    assert consume(monkeypatch, mode, direct_source(), brands=[brand])[0].brand == expected


@pytest.mark.parametrize("mode", ["legacy", "summary"])
@pytest.mark.parametrize("text", [
    "供应商名称 | 货物名称\n甲供应商 | 虚构服务",
    "--- HTML表格2 ---\n供应商名称 | 货物名称\n甲供应商 | 虚构服务",
    direct_source("甲供应商 | 虚构服务\n乙供应商 | 虚构服务"),
    direct_source("甲供应商 | 虚构服务扩展版"),
    direct_source("详见附件 | 虚构服务"),
    direct_source("未公示 | 虚构服务"),
    direct_source("未公示（以附件为准） | 虚构服务"),
    direct_source("未公示。 | 虚构服务"),
    direct_source("以附件为准 | 虚构服务"),
    direct_source("甲供应商 | 详见附件"),
    direct_source("甲供应商 | 虚构服务", header="采购人名称 | 货物名称"),
    direct_source("甲供应商 | 虚构服务", header="代理机构名称 | 货物名称"),
    direct_source("甲供应商 | 虚构服务", header="投标供应商名称 | 货物名称"),
    direct_source("甲供应商 | 虚构服务", header="供应商名称 | 报价货物名称"),
    direct_source(tail="\n" + metadata(project="另一个项目", number=3)),
    direct_source().replace("采购项目名称 | 虚构项目", "采购项目名称 | 详见附件（项目名称）"),
    direct_source().replace("四、主要标的信息", "四、投标报价排名"),
    direct_source().replace("--- HTML表格2 ---", "--- 其他公告 ---\n--- HTML表格2 ---"),
    "采购项目名称\n另一个项目\n" + direct_source(),
    "采购项目名称 | 另一个项目\n" + direct_source(),
    "--- 附件：其他公告.txt ---\n" + direct_source(),
    package_source(second_amount="100元"),
    package_source(award_heading="三、投标报价排名"),
    package_source().replace("中标金额", "预算金额"),
    package_source().replace("中标金额", "评审报价"),
    package_source().replace("名称：虚构服务", "名称：其他服务"),
    package_source().replace("供应商名称：甲供应商", "供应商名称：甲供应商\n供应商名称：丙供应商"),
])
def test_ambiguous_missing_scope_unmatched_item_or_nonaward_source_defers(monkeypatch, mode, text):
    assert consume(monkeypatch, mode, text)[0].brand == ""


@pytest.mark.parametrize("mode", ["legacy", "summary"])
@pytest.mark.parametrize("amount", ["100", "100元/年", "100元/台", "100元起", "100-200元", "78%", "预算100元", "1,00元"])
def test_nonexplicit_contract_total_cannot_disambiguate_same_name_packages(monkeypatch, mode, amount):
    assert consume(monkeypatch, mode, package_source(), amounts=[amount])[0].brand == ""


@pytest.mark.parametrize("mode", ["legacy", "summary"])
def test_supplier_fill_does_not_trigger_second_dedup(monkeypatch, mode):
    rows = consume(monkeypatch, mode, direct_source(), brands=["", "甲供应商"])
    assert len(rows) == 2
    assert [r.brand for r in rows] == ["甲供应商", "甲供应商"]


def test_attachment_details_do_not_inherit_award_supplier(monkeypatch):
    rows = consume(monkeypatch, "details", direct_source())
    assert rows[0].brand == ""
    assert (rows[0].package_name, rows[0].source_file, rows[0].source_page) == ("包A", "明细.txt", "3")


@pytest.mark.parametrize("mode", ["legacy", "summary"])
def test_same_row_package_supplier_conflicting_with_award_defers(monkeypatch, mode):
    text = ("三、中标信息\n标包：A\n供应商名称：乙供应商\n中标金额：100元\n"
            "四、主要标的信息\n标包：A\n供应商名称 | 货物名称\n甲供应商 | 虚构服务\n"
            "五、评审专家\n" + metadata()
            + "\n--- HTML表格2 ---\n供应商名称 | 货物名称\n甲供应商 | 虚构服务")
    assert consume(monkeypatch, mode, text)[0].brand == ""


@pytest.mark.parametrize("mode", ["legacy", "summary"])
def test_column_alternate_cannot_hide_multiple_inline_award_suppliers(monkeypatch, mode):
    text = ("三、中标信息\n标包：A\n供应商名称：甲供应商\n供应商名称：乙供应商\n"
            "中标金额：100元\n供应商名称\n中标金额(元)\n甲供应商\n100\n"
            "四、主要标的信息\n标包：A\n名称：虚构服务\n五、评审专家\n" + metadata()
            + "\n--- HTML表格2 ---\n供应商名称 | 中标金额(元)\n甲供应商 | 100")
    assert consume(monkeypatch, mode, text)[0].brand == ""


@pytest.mark.parametrize("mode", ["legacy", "summary"])
def test_joined_cell_substring_is_not_same_item_row_evidence(monkeypatch, mode):
    text = direct_source().replace("甲供应商 | 虚构服务", "甲供应商扩展 | 虚构服务", 1)
    text = text.replace("--- HTML表格2 ---\n供应商名称 | 货物名称\n甲供应商 | 虚构服务",
                        "--- HTML表格2 ---\n供应商名称 | 货物名称\n甲供应商 | 扩展虚构服务")
    assert consume(monkeypatch, mode, text, names=["扩展虚构服务"])[0].brand == ""


@pytest.mark.parametrize("mode", ["legacy", "summary"])
@pytest.mark.parametrize("other", ["乙供应商\n200", "预算金额\n200"])
def test_award_table_cannot_join_unordered_supplier_and_amount_cells(monkeypatch, mode, other):
    text = ("三、中标信息\n标包：A\n供应商名称\n中标金额(元)\n甲供应商\n100\n"
            + other + "\n四、主要标的信息\n标包：A\n名称：虚构服务\n五、评审专家\n"
            + metadata() + "\n--- HTML表格2 ---\n供应商名称 | 中标金额(元)\n甲供应商 | 200")
    assert consume(monkeypatch, mode, text, amounts=["200元"])[0].brand == ""


@pytest.mark.parametrize("mode", ["legacy", "summary"])
def test_award_table_subset_cannot_hide_second_body_award_row(monkeypatch, mode):
    text = ("三、中标信息\n标包：A\n供应商名称\n中标金额(元)\n甲供应商\n100\n"
            "乙供应商\n200\n四、主要标的信息\n标包：A\n名称：虚构服务\n五、评审专家\n"
            + metadata() + "\n--- HTML表格2 ---\n供应商名称 | 中标金额(元)\n甲供应商 | 100")
    assert consume(monkeypatch, mode, text)[0].brand == ""


@pytest.mark.parametrize("mode", ["legacy", "summary"])
@pytest.mark.parametrize("other", ["乙供应商", "详见附件", ""])
def test_item_table_subset_cannot_hide_other_same_name_body_row(monkeypatch, mode, other):
    text = direct_source().replace("甲供应商 | 虚构服务\n五、评审专家",
                                   "甲供应商 | 虚构服务\n" + other + " | 虚构服务\n五、评审专家")
    assert consume(monkeypatch, mode, text)[0].brand == ""


@pytest.mark.parametrize("mode", ["legacy", "summary"])
@pytest.mark.parametrize("other", ["乙供应商\n详见附件", "未公示\n200"])
def test_award_table_subset_retains_missing_supplier_or_amount_body_conflict(monkeypatch, mode, other):
    text = ("三、中标信息\n标包：A\n供应商名称\n中标金额(元)\n甲供应商\n100\n"
            + other + "\n四、主要标的信息\n标包：A\n名称：虚构服务\n五、评审专家\n"
            + metadata() + "\n--- HTML表格2 ---\n供应商名称 | 中标金额(元)\n甲供应商 | 100")
    assert consume(monkeypatch, mode, text)[0].brand == ""


@pytest.mark.parametrize("mode", ["legacy", "summary"])
def test_missing_supplier_in_selected_same_name_package_cannot_use_other_package(monkeypatch, mode):
    text = direct_source("A | 虚构服务 | 甲供应商 | 100元\nB | 虚构服务 | 详见附件 | 200元",
                         header="标包 | 货物名称 | 供应商名称 | 中标金额")
    assert consume(monkeypatch, mode, text, amounts=["200元"])[0].brand == ""


def test_helper_copies_type_and_source_metadata_without_mutating_input():
    from extractor.supplier_rules import fill_supplier_entities
    from models.schemas import DetailedEntity
    entity = DetailedEntity(product_name="虚构服务", category="服务/其他服务", spec_model="X-1",
                            unit_price="100元", quantity="1项", total_price="100元",
                            package_name="包A", source_file="证据.txt", source_page="3")
    filled = fill_supplier_entities([entity], source_text=direct_source())
    assert entity.brand == ""
    assert type(filled[0]) is DetailedEntity and filled[0] is not entity
    assert filled[0].model_dump() == {
        "product_name": "虚构服务", "category": "服务/其他服务", "brand": "甲供应商", "spec_model": "X-1",
        "unit_price": "100元", "quantity": "1项", "total_price": "100元",
        "package_name": "包A", "source_file": "证据.txt", "source_page": "3",
    }


@pytest.mark.parametrize("mode", ["legacy", "summary"])
def test_real_html_parser_and_serializer_keep_same_row_supplier_relationship(monkeypatch, mode):
    from data_loader.html_parser import HTMLAnnouncementParser
    from extractor.summary_rules import build_summary_source
    html = """<html><body><table><tr><td>公告信息：</td></tr>
    <tr><td>采购项目名称</td><td>虚构项目</td></tr>
    <tr><td>品目</td><td>服务/其他服务</td></tr></table>
    <div class="vF_detail_content"><p>四、主要标的信息</p><p>成交金额：100元</p>
    <table><tr><th>供应商名称</th><th>货物名称</th></tr>
    <tr><td>甲供应商</td><td>虚构服务</td></tr></table><p>五、评审专家</p></div></body></html>"""
    parsed = HTMLAnnouncementParser().parse_html(html, announcement_id="fictional-supplier-html")
    text = build_summary_source(parsed.html_text, parsed.html_tables)
    assert consume(monkeypatch, mode, text)[0].brand == "甲供应商"


def test_legacy_resume_invalidates_supplier_fill_version(tmp_path, monkeypatch):
    import pipeline as module
    html = _write_announcement(tmp_path, "fictional-supplier-legacy")
    pipeline = _pipeline(tmp_path, _RecordingExtractor())
    first = pipeline.process_single_file(str(html), run_mode="legacy")
    monkeypatch.setattr(module, "SUPPLIER_FILL_RULE_VERSION", "test-supplier-next", raising=False)
    second = pipeline.process_single_file(str(html), run_mode="legacy", previous_result=first, resume=True)
    assert first.summary_input_hash != second.summary_input_hash


def test_summary_resume_version_change_leaves_detail_fingerprint_unchanged(tmp_path, monkeypatch):
    import pipeline as module
    html = write_html(tmp_path, "fictional-supplier-both", "测试设备，成交金额100元")
    write_attachment_zip(html, "测试设备 | 100元 | 1台")
    pipeline = make_pipeline(tmp_path, RecordingExtractor())
    first = pipeline.process_single_file(str(html), run_mode="both")
    monkeypatch.setattr(module, "SUPPLIER_FILL_RULE_VERSION", "test-supplier-next", raising=False)
    second = pipeline.process_single_file(str(html), run_mode="both", previous_result=first, resume=True)
    assert first.summary_input_hash != second.summary_input_hash
    assert first.detail_input_hash == second.detail_input_hash
