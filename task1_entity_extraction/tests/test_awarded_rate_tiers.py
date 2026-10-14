"""Catch unconditional package collapse; literal sources, no model calls."""
import json

import pytest

from extractor.llm_extractor import LLMExtractor
from extractor.summary_rules import sanitize_summary_entities
from extractor.awarded_rate_tiers import keep_proven_rate_tiers
from models.schemas import ExtractedEntity


ONE_PACKAGE = (
    "项目名称：咨询服务\n三、中标（成交）信息\n第1包\n"
    "供应商名称：甲公司\n中标金额：12000元\n"
    "中标分档报价：资深人员800元/人/天；一般人员400元/人/天。\n"
    "四、主要标的信息\n名称：咨询服务（第1包）\n规格：详见附件\n"
)


def tier_rows(package=1, supplier="甲公司"):
    return [
        ExtractedEntity(product_name=f"咨询服务（第{package}包，资深人员）", brand=supplier,
                        spec_model="资深人员", unit_price="800元/人/天"),
        ExtractedEntity(product_name=f"咨询服务（第{package}包，一般人员）", brand=supplier,
                        spec_model="一般人员", unit_price="400元/人/天"),
    ]


def clean(rows, source=ONE_PACKAGE):
    return sanitize_summary_entities(rows, detail_status="no_attachment", source_text=source)


def guard_clean(rows, source):
    """V6 ambiguous-source fallback is not the old positional collapse.

    These negative tests pin the unchanged rate guard independently: given
    a collapsed owner, it must never promote unsupported rates. New V6 public
    tests separately require original model rows when collapse is unproved.
    All positive rate tests above/below still exercise the complete sanitizer.
    """
    collapsed = [ExtractedEntity(product_name="咨询服务（第1包）", brand="甲公司", total_price="12000元")]
    return keep_proven_rate_tiers(rows, source_text=source, collapsed_packages=collapsed)


def test_explicit_awarded_tiers_survive_numeric_package_total():
    rows = tier_rows()
    before = [row.model_dump() for row in rows]
    result = clean(rows)
    assert [row.product_name for row in result] == [
        "咨询服务（第1包，资深人员）", "咨询服务（第1包，一般人员）",
    ]
    assert [row.unit_price for row in result] == ["800元/人/天", "400元/人/天"]
    assert [row.spec_model for row in result] == ["资深人员", "一般人员"]
    assert all(row.quantity == row.total_price == "" for row in result)
    assert [row.model_dump() for row in rows] == before


def test_minimal_unsectioned_explicit_awarded_rate_block_is_supported():
    source = (
        "项目名称：咨询服务（第1包）\n供应商名称：甲公司\n中标金额：12000元\n"
        "中标分档报价：资深人员800元/人/天；一般人员400元/人/天。"
    )
    assert [row.unit_price for row in clean(tier_rows(), source)] == ["800元/人/天", "400元/人/天"]


def test_identical_supplier_tiers_in_two_explicit_packages_stay_separate():
    source = (
        "项目名称：咨询服务\n三、中标信息\n第1包\n供应商名称：甲公司\n中标金额：12000元\n"
        "中标分档报价：资深人员800元/人/天；一般人员400元/人/天。\n"
        "第2包\n供应商名称：甲公司\n中标金额：24000元\n"
        "中标分档报价：资深人员800元/人/天；一般人员400元/人/天。\n四、主要标的信息\n详见附件"
    )
    rows = list(reversed(tier_rows(1) + tier_rows(2)))
    result = clean(rows, source)
    assert [row.product_name for row in result] == [
        "咨询服务（第2包，一般人员）", "咨询服务（第2包，资深人员）",
        "咨询服务（第1包，一般人员）", "咨询服务（第1包，资深人员）",
    ]
    assert [row.unit_price for row in result] == ["400元/人/天", "800元/人/天", "400元/人/天", "800元/人/天"]


def test_mixed_packages_protect_only_rates_and_still_collapse_devices():
    source = (
        "项目名称：咨询及设备项目\n三、中标信息\n第1包\n供应商名称：甲公司\n中标金额：12000元\n"
        "成交分档报价：资深人员800元/人/天；一般人员400元/人/天。\n"
        "第2包\n供应商名称：乙公司\n成交金额：30000元\n四、主要标的信息\n详见附件"
    )
    rows = tier_rows() + [ExtractedEntity(product_name="附件设备", brand="乙公司", unit_price="1000元")]
    result = clean(rows, source)
    assert [row.unit_price for row in result if row.brand == "甲公司"] == ["800元/人/天", "400元/人/天"]
    ordinary = [row for row in result if row.brand == "乙公司"]
    assert len(ordinary) == 1
    assert "第2包" in ordinary[0].product_name
    assert ordinary[0].total_price == "30000元"
    assert ordinary[0].unit_price == ordinary[0].spec_model == ""
    assert not any(row.product_name == "附件设备" for row in result)


@pytest.mark.parametrize("bad_label", ["预算分档报价", "最高限价", "未中标报价", "投标报价", "报价模板"])
def test_non_awarded_rates_cannot_exempt_rows_from_package_collapse(bad_label):
    source = ONE_PACKAGE.replace("中标分档报价", bad_label)
    result = guard_clean(tier_rows(), source)
    assert len(result) == 1
    assert result[0].unit_price == result[0].spec_model == ""
    assert [row.model_dump() for row in clean(tier_rows(), source)] == [row.model_dump() for row in tier_rows()]


def test_rates_only_in_attachment_cannot_exempt_summary_rows():
    source = ONE_PACKAGE.replace("中标分档报价：资深人员800元/人/天；一般人员400元/人/天。", "")
    source += "\n--- 附件：报价.pdf ---\n" + ONE_PACKAGE
    result = guard_clean(tier_rows(), source)
    # Attachment rates never promote a collapsed owner; V6's independent
    # scope gate may instead retain unmodified model rows for invalid inputs.
    assert all(row.unit_price == row.spec_model == "" for row in result)


@pytest.mark.parametrize("change", ["package", "supplier", "denominator", "tier", "combined"])
def test_wrong_identity_or_rate_does_not_receive_protection(change):
    rows = tier_rows()
    for row in rows:
        if change == "package":
            row.product_name = row.product_name.replace("第1包", "第2包")
        elif change == "supplier":
            row.brand = "错误公司"
        elif change == "denominator":
            row.unit_price = row.unit_price.replace("/人/天", "/项")
        elif change == "tier":
            row.product_name = row.product_name.replace("资深人员", "未知人员").replace("一般人员", "未知人员")
            row.spec_model = "未知人员"
        else:
            row.unit_price = "800元/人/天；400元/人/天"
    result = clean(rows)
    assert len(result) == 1
    assert result[0].unit_price == ""


def test_same_package_duplicate_rate_rows_are_stably_deduplicated():
    first, second = tier_rows()
    result = clean([first, first.model_copy(deep=True), second])
    assert [row.unit_price for row in result] == ["800元/人/天", "400元/人/天"]


@pytest.mark.parametrize("conflict", ["source_supplier", "source_package", "source_tier"])
def test_conflicting_source_evidence_fails_closed(conflict):
    if conflict == "source_supplier":
        source = ONE_PACKAGE.replace("供应商名称：甲公司", "供应商名称：甲公司\n供应商名称：乙公司")
    elif conflict == "source_package":
        source = ONE_PACKAGE.replace("第1包\n", "第1包、第2包\n", 1)
    else:
        source = ONE_PACKAGE.replace("一般人员400元/人/天", "资深人员400元/人/天")
    result = guard_clean(tier_rows(), source)
    assert all(not row.unit_price for row in result)


def test_extract_summary_keeps_tiers_through_complete_cleanup_chain(monkeypatch):
    extractor = object.__new__(LLMExtractor)
    response = json.dumps({"entities": [row.model_dump() for row in tier_rows()]}, ensure_ascii=False)
    # Only external inference is replaced; parsing and all cleanup stay real.
    monkeypatch.setattr(extractor, "_call_llm", lambda *args: response)
    result = extractor.extract_summary(ONE_PACKAGE, has_tables=False, detail_status="no_attachment", announcement_id="synthetic-rates")
    assert result.success
    assert result.raw_response == response
    assert [row.unit_price for row in result.entities] == ["800元/人/天", "400元/人/天"]


@pytest.mark.parametrize("context", ["招标需求范本", "报价模板", "预算最高限价"])
def test_negative_context_in_award_block_cannot_establish_tier_evidence(context):
    source = ONE_PACKAGE.replace("中标分档报价：", f"说明：{context}\n中标分档报价：")
    assert all(not row.unit_price for row in guard_clean(tier_rows(), source))


@pytest.mark.parametrize("bad_identity", ["missing", "conflicting"])
def test_model_missing_or_conflicting_package_cannot_be_repaired_by_guard(bad_identity):
    rows = tier_rows()
    for row in rows:
        row.product_name = row.product_name.replace("第1包", "" if bad_identity == "missing" else "第1包、第2包")
    assert all(not row.unit_price for row in clean(rows))


def test_repeated_source_tier_fields_are_ambiguous_not_deduplicated():
    source = ONE_PACKAGE.replace("四、主要标的信息", "中标分档报价：资深人员800元/人/天；一般人员400元/人/天。\n四、主要标的信息")
    assert all(not row.unit_price for row in clean(tier_rows(), source))


@pytest.mark.parametrize("table", [
    "公告信息\n采购项目名称 | 另一项目",
    "公告信息\n采购项目名称 | 咨询服务\n--- HTML表格2 ---\n中标分档报价 | 资深人员999元/人/天；一般人员400元/人/天。",
    "公告信息\n采购项目名称 | 咨询服务\n--- HTML表格2 ---\n中标分档报价 | 资深人员800元/人/天 | 一般人员400元/人/天",
])
def test_inconsistent_or_partial_serialized_html_fails_closed(table):
    source = ONE_PACKAGE + "\n--- HTML表格1 ---\n" + table
    assert all(not row.unit_price for row in guard_clean(tier_rows(), source))


@pytest.mark.parametrize("prefix", ["报价模板如下：", "预算最高限价如下：", "供应商名称：乙公司", "中标分档报价：其他人员200元/人/天；一般人员400元/人/天。"])
def test_ambiguous_or_negative_prefix_before_package_is_not_discarded(prefix):
    source = ONE_PACKAGE.replace("第1包\n", prefix + "\n第1包\n", 1)
    assert all(not row.unit_price for row in guard_clean(tier_rows(), source))


def test_package_total_is_not_repeated_on_each_protected_rate_tier():
    rows = tier_rows()
    for row in rows:
        row.total_price = "12000元"
    result = clean(rows)
    assert [row.unit_price for row in result] == ["800元/人/天", "400元/人/天"]
    assert [row.total_price for row in result] == ["", ""]
    assert [row.total_price for row in rows] == ["12000元", "12000元"]


@pytest.mark.parametrize("table", [
    "采购包 | 供应商名称 | 中标金额\n第1包 | 乙公司 | 12000元",
    "供应商名称 | 乙公司",
])
def test_competing_serialized_award_identity_disables_tier_protection(table):
    source = ONE_PACKAGE + "\n--- HTML表格1 ---\n公告信息\n采购项目名称 | 咨询服务\n--- HTML表格2 ---\n" + table
    assert all(not row.unit_price for row in guard_clean(tier_rows(), source))


def test_unknown_marker_cannot_bypass_html_scope_validation():
    source = ONE_PACKAGE + "\n--- UNKNOWN ---\n--- HTML表格1 ---\n公告信息\n采购项目名称 | 另一项目"
    assert all(not row.unit_price for row in guard_clean(tier_rows(), source))
    assert [row.model_dump() for row in clean(tier_rows(), source)] == [row.model_dump() for row in tier_rows()]


def test_consistent_project_metadata_and_exact_rate_field_table_are_supported():
    source = ONE_PACKAGE + (
        "\n--- HTML表格1 ---\n公告信息\n采购项目名称 | 咨询服务\n"
        "--- HTML表格2 ---\n中标分档报价 | 资深人员800元/人/天；一般人员400元/人/天。"
    )
    assert [row.unit_price for row in clean(tier_rows(), source)] == ["800元/人/天", "400元/人/天"]


@pytest.mark.parametrize("declaration", ["成交供应商：乙公司", "中标供应商：乙公司", "供应商名称\n乙公司"])
@pytest.mark.parametrize("placement", ["prefix", "inside"])
def test_unsupported_supplier_declarations_never_disappear(declaration, placement):
    if placement == "prefix":
        source = ONE_PACKAGE.replace("第1包\n", declaration + "\n第1包\n", 1)
    else:
        source = ONE_PACKAGE.replace("中标分档报价：", declaration + "\n中标分档报价：", 1)
    assert all(not row.unit_price for row in guard_clean(tier_rows(), source))
    # Unsupported owners now prevent V6 collapse altogether. Retaining an
    # existing model rate is not source certification by the unchanged guard.
    assert [row.model_dump() for row in clean(tier_rows(), source)] == [row.model_dump() for row in tier_rows()]


@pytest.mark.parametrize("table", [
    "公告信息\n采购项目名称 | 咨询服务\n备注 | 第1包成交供应商：乙公司",
    "公告信息\n采购项目名称 | 咨询服务\n备注 | 本包中标人为乙公司",
    "公告信息\n采购项目名称 | 咨询服务\n--- HTML表格2 ---\n中标人 | 金额\n乙公司 | 12000元",
])
def test_unknown_metadata_notes_and_award_matrices_fail_closed(table):
    source = ONE_PACKAGE + "\n--- HTML表格1 ---\n" + table
    assert all(not row.unit_price for row in guard_clean(tier_rows(), source))
    assert [row.model_dump() for row in clean(tier_rows(), source)] == [row.model_dump() for row in tier_rows()]


def test_project_metadata_with_an_explicit_package_label_is_not_an_award_conflict():
    source = ONE_PACKAGE.replace("项目名称：咨询服务\n", "项目名称：咨询服务（第1包）\n")
    source += "\n--- HTML表格1 ---\n公告信息\n采购项目名称 | 咨询服务（第1包）"
    assert [row.unit_price for row in clean(tier_rows(), source)] == ["800元/人/天", "400元/人/天"]


@pytest.mark.parametrize("qualifier", [
    "说明：以上为投标报价", "说明：本表仅为候选人报价，非最终结果",
    "1.以上为投标报价", "1.说明：本表仅为候选人报价，非最终结果", "1.重要说明",
])
def test_negative_tail_of_award_section_is_not_discarded(qualifier):
    source = ONE_PACKAGE.replace("四、主要标的信息", qualifier + "\n四、主要标的信息")
    assert all(not row.unit_price for row in guard_clean(tier_rows(), source))
