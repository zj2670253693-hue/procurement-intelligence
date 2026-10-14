"""Preserve existing HTML item totals only with unique, complete source proof.

All fixtures are fictional. No model, attachment parser or network is called;
the public summary cleanup and its real HTML-source serialization stay intact.
"""

import pytest

from extractor.summary_rules import build_summary_source, sanitize_summary_entities
from models.schemas import ExtractedEntity


PROJECT = "虚构维护项目"
NAME = "维护服务（第1包）"


def item_row(*, name=NAME, supplier="甲公司", package="第1包", total="8600元", serial="1"):
    return [serial, supplier, name, package, "VX-01", "4300元", "2项", total]


def entity(*, name=NAME, supplier="甲公司", total="8600元"):
    return ExtractedEntity(
        product_name=name, brand=supplier, spec_model="VX-01",
        unit_price="4300元", quantity="2项", total_price=total,
    )


def source(*, rows=None, body_rows=None, serialized_rows=None, amount_header="总价",
           tail="", metadata_project=PROJECT, include_body_table=True):
    """Complete body/table witnesses; variants change only the intended proof."""
    rows = [item_row()] if rows is None else rows
    body_rows = rows if body_rows is None else body_rows
    serialized_rows = rows if serialized_rows is None else serialized_rows
    header = ["序号", "供应商名称", "名称", "采购包", "规格型号", "单价", "数量", amount_header]
    body = (
        f"项目名称：{PROJECT}\n三、中标信息\n第1包\n供应商名称：甲公司\n"
        "四、主要标的信息\n"
    )
    if include_body_table:
        body += "\n".join(" | ".join(row) for row in [header, *body_rows]) + "\n"
    if tail:
        body += tail + "\n"
    body += "五、评审专家名单\n虚构专家\n"
    metadata = [["公告信息"], ["采购项目名称", metadata_project]]
    return build_summary_source(body, [metadata, [header, *serialized_rows]])


def clean(rows, text):
    return sanitize_summary_entities(rows, detail_status="no_attachment", source_text=text)


def other_fields(row):
    return {key: value for key, value in row.model_dump().items() if key != "total_price"}


@pytest.mark.parametrize("header,cell,total", [
    ("总价", "8600元", "8600元"),
    ("金额", "8600元", "8600元"),
    ("总价(元)", "8600", "8600"),
    ("金额(元)", "8600", "8600"),
    ("总价", "8,600.00元", "8,600.00元"),
])
def test_existing_total_survives_unique_main_item_total_or_amount_column(header, cell, total):
    original = entity(total=total)
    before = original.model_dump()
    result = clean([original], source(rows=[item_row(total=cell)], amount_header=header))
    assert len(result) == 1
    assert result[0].total_price == total
    assert other_fields(result[0]) == other_fields(original)
    assert original.model_dump() == before


def test_header_currency_preserves_existing_numeric_spelling_without_appending_a_unit():
    original = entity(total="8600.00")
    result = clean([original], source(rows=[item_row(total="8600")], amount_header="总价(元)"))
    assert result[0].total_price == "8600.00"
    assert other_fields(result[0]) == other_fields(original)


def test_procurement_result_contract_package_and_vertical_goods_table_support_existing_total():
    header = ["品目号", "品目名称", "采购标的", "品牌", "规格型号", "数量(单位)", "单价(元)", "总价(元)"]
    cells = ["1-1", "设备维护类", "维护终端", "样例品牌", "VX-01", "2(台)", "4300", "8600"]
    body = (
        f"项目名称：{PROJECT}\n三、采购结果\n合同包1(设备维护):\n供应商名称：甲公司\n"
        "四、主要标的信息\n合同包1(设备维护):\n货物类(甲公司)\n"
        + "\n".join([*header, *cells])
        + "\n五、评审专家名单\n虚构专家\n"
    )
    text = build_summary_source(body, [
        [["公告信息"], ["采购项目名称", PROJECT]],
        [header, cells],
    ])
    original = ExtractedEntity(
        product_name="维护终端", category="设备维护类", brand="样例品牌",
        spec_model="VX-01", unit_price="4300", quantity="2台", total_price="8600",
    )
    before = original.model_dump()
    result = clean([original], text)
    assert len(result) == 1
    assert result[0].total_price == "8600"
    assert other_fields(result[0]) == other_fields(original)
    assert original.model_dump() == before


@pytest.mark.parametrize("changes", [
    {"name": "别的服务（第1包）"},
    {"name": "维护服务（第2包）"},
    {"supplier": "错误公司"},
    {"total": "9900元"},
])
def test_wrong_item_package_supplier_or_amount_does_not_borrow_another_row_total(changes):
    original = entity(**changes)
    result = clean([original], source())
    assert len(result) == 1
    assert result[0].total_price == ("8600元" if changes == {"total": "9900元"} else "")
    assert other_fields(result[0]) == other_fields(original)


@pytest.mark.parametrize("second_total", ["9900元", "8600元"])
def test_duplicate_same_item_rows_are_ambiguous_even_when_the_model_amount_matches(second_total):
    rows = [item_row(), item_row(total=second_total, serial="2")]
    result = clean([entity()], source(rows=rows))
    assert result[0].total_price == ""


def test_same_name_in_two_packages_cannot_be_disambiguated_by_matching_amount():
    rows = [
        item_row(name="维护服务", package="第1包", total="8600元"),
        item_row(name="维护服务", package="第2包", total="9900元", serial="2"),
    ]
    result = clean([entity(name="维护服务")], source(rows=rows))
    assert result[0].total_price == ""


@pytest.mark.parametrize("header", ["预算", "预算金额", "最高限价", "投标报价"])
def test_other_money_column_roles_are_not_explicit_item_totals(header):
    result = clean([entity()], source(amount_header=header))
    assert result[0].total_price == ""


@pytest.mark.parametrize("tail", [
    "说明：以上金额为预算，仅供参考。",
    "说明：以上金额是最高限价，并非成交结果。",
    "说明：以上为候选人报价，并非最终结果。",
    "1.说明：本表仅为候选人报价，并非最终结果。",
])
def test_budget_ceiling_or_candidate_tail_cannot_certify_the_preceding_item_table(tail):
    result = clean([entity()], source(tail=tail))
    assert result[0].total_price == ""


@pytest.mark.parametrize("model_total", ["8600", "8600元"])
def test_unitless_source_cell_and_unitless_header_cannot_borrow_currency_from_the_model(model_total):
    result = clean([entity(total=model_total)], source(rows=[item_row(total="8600")]))
    assert result[0].total_price == ""


@pytest.mark.parametrize("body_rows,serialized_rows", [
    ([item_row(name="别的服务（第1包）")], [item_row()]),
    ([item_row(total="9900元")], [item_row()]),
    ([item_row()], [item_row(total="9900元")]),
    ([item_row()], []),
    ([item_row()[:-1]], [item_row()]),
    ([item_row()], [item_row()[:-1]]),
])
def test_detached_conflicting_or_partial_body_and_serialized_rows_do_not_prove_a_total(body_rows, serialized_rows):
    result = clean([entity()], source(body_rows=body_rows, serialized_rows=serialized_rows))
    assert result[0].total_price == ""


def test_serialized_table_without_a_main_item_body_counterpart_is_not_proof():
    result = clean([entity()], source(include_body_table=False))
    assert result[0].total_price == ""


def test_mismatching_project_metadata_cannot_certify_the_item_total():
    result = clean([entity()], source(metadata_project="另一个虚构项目"))
    assert result[0].total_price == ""


def test_attachment_total_is_not_used_but_proved_html_row_can_supply_its_subtotal():
    text = source(rows=[item_row(total="")])
    attachment_header = ["序号", "供应商名称", "名称", "采购包", "规格型号", "单价", "数量", "总价"]
    text += "\n\n--- 附件：虚构明细.txt ---\n四、主要标的信息\n" + "\n".join(
        " | ".join(row) for row in [attachment_header, item_row()]
    )
    result = clean([entity()], text)
    assert result[0].total_price == "8600元"  # HTML 4300元 × 2项; not the attachment.


def test_explicit_item_total_fills_an_empty_model_total_under_v12():
    original = entity(total="")
    result = clean([original], source())
    assert len(result) == 1
    assert result[0].total_price == "8600元"
    assert other_fields(result[0]) == other_fields(original)


def test_unique_awarded_row_price_times_quantity_is_v12_total_evidence():
    result = clean([entity()], source(rows=[item_row(total="")]))
    assert result[0].total_price == "8600元"


def test_rejected_existing_total_does_not_mutate_the_input_or_other_fields():
    original = entity(total="9900元")
    before = original.model_dump()
    result = clean([original], source())
    assert result[0].total_price == "8600元"
    assert other_fields(result[0]) == other_fields(original)
    assert original.model_dump() == before


def test_separate_contract_package_tables_with_the_same_header_keep_both_unique_items():
    header = ["品目号", "采购标的", "品牌", "规格型号", "总价(元)"]
    first = ["1-1", "终端甲", "样例品牌", "VX-01", "8600"]
    second = ["2-1", "终端乙", "样例品牌", "VX-01", "9900"]
    body = (f"项目名称：{PROJECT}\n三、采购结果\n供应商名称：甲公司\n"
            "四、主要标的信息\n合同包1(设备甲):\n"
            + "\n".join([*header, *first])
            + "\n合同包2(设备乙):\n" + "\n".join([*header, *second])
            + "\n五、评审专家名单\n虚构专家")
    text = build_summary_source(body, [
        [["公告信息"], ["采购项目名称", PROJECT]], [header, first], [header, second],
    ])
    inputs = [entity(name="终端甲", supplier="样例品牌", total="8600"),
              entity(name="终端乙", supplier="样例品牌", total="9900")]
    result = clean(inputs, text)
    assert [row.total_price for row in result] == ["8600", "9900"]
    assert [other_fields(row) for row in result] == [other_fields(row) for row in inputs]


@pytest.mark.parametrize("partial", [True, False])
def test_partial_or_detached_competing_same_name_row_is_negative_evidence(partial):
    competing = item_row(serial="2", total="9900元")
    if partial:
        text = source(body_rows=[item_row(), competing[:-1]], serialized_rows=[item_row()])
    else:
        text = source(body_rows=[item_row()], serialized_rows=[item_row(), competing])
    result = clean([entity()], text)
    assert len(result) == 1
    assert result[0].total_price == ""


@pytest.mark.parametrize("disclaimer", [
    "以上主要标的信息仅为候选人报价，并非最终结果。",
    "本表金额为预算，非成交总价。",
    "上述主要标的信息为最高限价。",
])
def test_table_referencing_negative_disclaimer_after_section_boundary_rejects(disclaimer):
    text = source().replace("五、评审专家名单\n虚构专家", "五、其他补充事宜\n" + disclaimer)
    assert clean([entity()], text)[0].total_price == ""


def test_candidate_announcement_title_before_award_section_rejects():
    text = "中标候选人公示\n" + source()
    assert clean([entity()], text)[0].total_price == ""


def test_item_supplier_column_conflicting_with_service_owner_context_rejects():
    text = source().replace("四、主要标的信息\n", "四、主要标的信息\n服务类(乙公司)\n")
    assert clean([entity()], text)[0].total_price == ""


def test_mirrored_serialized_table_does_not_duplicate_the_same_body_row():
    text = source()
    second_table = text[text.index("--- HTML表格2 ---"):].replace("HTML表格2", "HTML表格3")
    text += "\n\n" + second_table
    assert clean([entity()], text)[0].total_price == "8600元"


def test_duplicate_rows_inside_one_serialized_table_do_not_prove_unique_identity():
    text = source(body_rows=[item_row()], serialized_rows=[item_row(), item_row()])
    assert clean([entity()], text)[0].total_price == ""


@pytest.mark.parametrize("other_changes", [{"brand": "乙公司"}, {"spec_model": "VX-02"}])
def test_nonunique_original_model_name_cannot_be_resolved_by_brand_spec_or_amount(other_changes):
    first = entity()
    second = entity(total="9900元").model_copy(update=other_changes)
    originals = [first, second]
    before = [row.model_dump() for row in originals]
    result = clean(originals, source())
    assert len(result) == 2
    assert [row.total_price for row in result] == ["", ""]
    assert [other_fields(row) for row in result] == [other_fields(row) for row in originals]
    assert [row.model_dump() for row in originals] == before


def test_preserving_a_total_must_not_change_the_existing_dedup_after_total_clear():
    originals = [entity(), entity(total="9900元")]
    before = [row.model_dump() for row in originals]
    result = clean(originals, source())
    assert len(result) == 1
    assert result[0].total_price == ""
    assert other_fields(result[0]) == other_fields(originals[0])
    assert [row.model_dump() for row in originals] == before


def test_unique_source_supplier_and_explicit_total_can_fill_the_v12_record():
    original = entity(supplier="")
    before = original.model_dump()
    result = clean([original], source())
    assert len(result) == 1
    assert result[0].brand == "甲公司"  # Keep the pre-existing supplier fill.
    assert result[0].total_price == "8600元"
    assert original.model_dump() == before


def test_a_contradictory_explicit_model_spec_does_not_receive_total_protection():
    original = entity().model_copy(update={"spec_model": "VX-02"})
    before = original.model_dump()
    result = clean([original], source())
    assert len(result) == 1
    assert result[0].total_price == ""
    assert other_fields(result[0]) == other_fields(original)
    assert original.model_dump() == before
