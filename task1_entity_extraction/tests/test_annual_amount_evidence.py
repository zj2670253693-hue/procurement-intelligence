"""Keep a literal annual award only after project/package/owner proof.

All fixtures are fictional. These tests catch omission of the final restoration
pass, loss of ownership checks, and a stale base cache after a rule change.
"""
import pytest

from extractor.summary_rules import build_summary_source, sanitize_summary_entities
from models.schemas import ExtractedEntity


PROJECT = "虚构通信线路租赁项目"


def rows():
    return [
        ExtractedEntity(product_name=PROJECT + "（包1）", brand="甲公司", total_price="1.0000万元"),
        ExtractedEntity(product_name=PROJECT + "（包2）", brand="乙公司", total_price="2.0000万元"),
    ]


def source(*, annual=True, first="10,000.00元/年", second="20,000.00 元/年", extra="", metadata=PROJECT):
    header = ["序号", "供应商名称", "服务名称", "服务范围", "服务要求", "服务时间", "服务标准"]
    items = [
        ["1", "甲公司", PROJECT, PROJECT + "包1", "具体详见采购文件", "具体详见采购文件", "具体详见采购文件"],
        ["2", "乙公司", PROJECT, PROJECT + "包2", "具体详见采购文件", "具体详见采购文件", "具体详见采购文件"],
    ]
    body = (f"二、项目名称：{PROJECT}\n三、中标（成交）信息\n"
            "供应商名称：甲公司\n供应商地址：虚构地址甲\n中标（成交）金额：1.0000（万元）\n"
            "供应商名称：乙公司\n供应商地址：虚构地址乙\n中标（成交）金额：2.0000（万元）\n"
            "四、主要标的信息\n" + "\n".join(" | ".join(row) for row in [header, *items]) +
            "\n五、评审专家名单\n虚构专家\n八、其它补充事宜\n")
    if annual:
        body += (f"1.{PROJECT}包1\n成交供应商：甲公司\n成交金额：{first}\n"
                 f"2.{PROJECT}包2\n成交供应商：乙公司\n成交金额：{second}\n")
    body += extra + "\n九、采购人信息\n虚构单位\n"
    return build_summary_source(body, [[["公告信息"], ["采购项目名称", metadata]], [header, *items]])


def clean(original, text):
    return sanitize_summary_entities(original, detail_status="no_attachment", source_text=text)


def dump(items):
    return [item.model_dump() for item in items]


def test_complete_annual_award_literal_replaces_only_equivalent_denominator_free_total():
    original = rows()
    before = dump(original)
    result = clean(original, source())
    assert [r.total_price for r in result] == ["10,000.00元/年", "20,000.00 元/年"]
    assert [{k: v for k, v in r.model_dump().items() if k != "total_price"} for r in result] == [
        {k: v for k, v in r.items() if k != "total_price"} for r in before]
    assert dump(original) == before


def test_reversing_result_rows_does_not_reassign_package_or_annual_award():
    result = clean(list(reversed(rows())), source())
    assert [(r.brand, r.total_price) for r in result] == [("乙公司", "20,000.00 元/年"), ("甲公司", "10,000.00元/年")]


@pytest.mark.parametrize("first", [
    "10,000.00元/月", "10,000.00元/人/年", "预算10,000.00元/年", "10,000.00元/年（候选报价）",
    "10,000.00元/年起", "10,000.00-20,000.00元/年", "10,00.00元/年", "10 000元/年", "10,001.00元/年",
])
def test_unsupported_or_mismatching_periodic_declaration_cannot_correct_existing_amount(first):
    assert clean(rows(), source(first=first))[0].total_price == "1.0000万元"


@pytest.mark.parametrize("replacement", [
    "成交供应商：丙公司", "成交供应商：甲公司\n成交供应商：丙公司", "成交供应商：甲公司\n中标人：丙公司",
])
def test_conflicting_or_unparsed_owner_is_not_discarded(replacement):
    text = source().replace("成交供应商：甲公司", replacement)
    assert clean(rows(), text)[0].total_price == "1.0000万元"


@pytest.mark.parametrize("heading", ["虚构其他项目包1", PROJECT + "包2", PROJECT + "包1包2", PROJECT])
def test_wrong_missing_or_ambiguous_source_package_identity_is_not_guessed(heading):
    text = source().replace("1." + PROJECT + "包1", "1." + heading)
    assert clean(rows(), text)[0].total_price == "1.0000万元"


def test_model_name_without_package_cannot_match_by_supplier_or_amount():
    original = rows()
    original[0].product_name = PROJECT
    assert clean(original, source())[0].total_price == "1.0000万元"


def test_other_project_metadata_cannot_certify_annual_award():
    assert clean(rows(), source(metadata="另一项目"))[0].total_price == "1.0000万元"


def test_attachment_annual_award_is_not_primary_html_evidence():
    text = source(annual=False) + "\n--- 附件：报价.txt ---\n" + source()
    assert [r.total_price for r in clean(rows(), text)] == ["1.0000万元", "2.0000万元"]


def test_service_duration_alone_does_not_add_period_or_compute_total():
    result = clean(rows(), source(annual=False, extra="服务期限为3年，合同每年1签。"))
    assert [r.total_price for r in result] == ["1.0000万元", "2.0000万元"]


def test_existing_periodic_amount_keeps_its_original_literal():
    original = rows()
    original[0].total_price = "1.0000万元/年"
    assert clean(original, source())[0].total_price == "1.0000万元/年"


def test_existing_monthly_amount_is_not_reinterpreted_as_annual():
    original = rows()
    original[0].total_price = "1.0000万元/月"
    assert clean(original, source())[0].total_price == "1.0000万元/月"


def test_empty_total_remains_empty_not_filled_with_an_annual_quote():
    original = rows()
    original[0].total_price = ""
    assert clean(original, source())[0].total_price == ""


def test_competing_annual_award_of_same_package_vetoes_even_if_one_matches():
    extra = f"3.{PROJECT}包1\n成交供应商：甲公司\n成交金额：11,000.00元/年"
    assert clean(rows(), source(extra=extra))[0].total_price == "1.0000万元"


def test_negative_qualifier_after_valid_annual_block_vetoes_correction():
    assert clean(rows(), source(extra="说明：以上为候选人报价，非最终成交结果。"))[0].total_price == "1.0000万元"


def test_unsupported_competing_amount_declaration_does_not_disappear():
    assert clean(rows(), source(extra="成交金额：30,000元/年"))[0].total_price == "1.0000万元"


@pytest.mark.parametrize("extra", [
    "中标人\n丙公司", "成交金额\n30,000元/月", "成交总金额：30,000元/月",
    "中标总金额：30,000元/月", "成交总价：30,000元/月", "成交报价：30,000元/月",
    "中标人 | 成交金额\n丙公司 | 30,000元/月",
])
def test_line_wrapped_or_alternative_competing_declarations_are_vetoes(extra):
    assert [r.total_price for r in clean(rows(), source(extra=extra))] == ["1.0000万元", "2.0000万元"]


def test_explicit_conflicting_primary_award_package_context_is_not_discarded():
    text = source().replace("供应商名称：甲公司", "第2包\n供应商名称：甲公司", 1)
    assert clean(rows(), text)[0].total_price == "1.0000万元"


@pytest.mark.parametrize("layout", ["both", "body-only", "table-only"])
def test_conflicting_main_item_package_owner_vetoes_annual_copy(layout):
    body, marker, table_text = source().partition("--- HTML表格1 ---")
    old = "1 | 甲公司 | " + PROJECT + " | " + PROJECT + "包1"
    new = "1 | 丙公司 | " + PROJECT + " | " + PROJECT + "包1"
    if layout in {"both", "body-only"}:
        body = body.replace(old, new)
    if layout in {"both", "table-only"}:
        table_text = table_text.replace(old, new)
    assert clean(rows(), body + marker + table_text)[0].total_price == "1.0000万元"


def test_table_only_unknown_owner_or_price_declaration_is_not_hidden():
    text = source() + "\n--- HTML表格3 ---\n采购包 | 中标人 | 成交金额\n第1包 | 丙公司 | 30,000元/月"
    assert clean(rows(), text)[0].total_price == "1.0000万元"


def vertical_source(*, extra=""):
    body, marker, tables = source(extra=extra).partition("--- HTML表格1 ---")
    start, item_marker, rest = body.partition("四、主要标的信息\n")
    item_rows, end_marker, end = rest.partition("五、评审专家名单")
    vertical = "\n".join(cell.strip() for line in item_rows.splitlines() for cell in line.split("|"))
    return start + item_marker + vertical + "\n" + end_marker + end + marker + tables


def test_complete_vertical_item_layout_can_corroborate_annual_package_owner():
    assert [r.total_price for r in clean(rows(), vertical_source())] == ["10,000.00元/年", "20,000.00 元/年"]


def test_item_header_text_cannot_hide_same_label_unparsed_in_another_section():
    text = vertical_source(extra="供应商名称\n丙公司")
    assert [r.total_price for r in clean(rows(), text)] == ["1.0000万元", "2.0000万元"]


@pytest.mark.parametrize("mode", ["summary", "both", "legacy"])
def test_annual_rule_change_invalidates_base_cache_without_invalidating_details(tmp_path, monkeypatch, mode):
    import pipeline as pipeline_module
    from tests.test_dual_layer_pipeline import RecordingExtractor, make_pipeline, write_html, write_attachment_zip
    from tests.test_legacy_base_layer import _RecordingExtractor, _pipeline

    html = write_html(tmp_path, "fictional-annual-resume-" + mode, "采购包一")
    write_attachment_zip(html)
    extractor = _RecordingExtractor() if mode == "legacy" else RecordingExtractor()
    pipe = _pipeline(tmp_path, extractor) if mode == "legacy" else make_pipeline(tmp_path, extractor)
    first = pipe.process_single_file(str(html), run_mode=mode)
    monkeypatch.setattr(pipeline_module, "ANNUAL_AMOUNT_RULE_VERSION", "fictional-next-annual-rule", raising=False)
    second = pipe.process_single_file(str(html), run_mode=mode, previous_result=first, resume=True)
    calls = extractor.legacy_calls if mode == "legacy" else extractor.summary_calls
    assert len(calls) == 2
    assert first.summary_input_hash != second.summary_input_hash
    assert first.detail_input_hash == second.detail_input_hash
    if mode == "both":
        assert len(extractor.detail_calls) == 1
