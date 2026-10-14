"""Package collapse needs complete body-owned identity, never positional pairs.

Fictional sources exercise the real summary cleanup; no model API is called.
"""

import pytest

from extractor.summary_rules import build_summary_source, sanitize_summary_entities
from models.schemas import ExtractedEntity


PROJECT = "虚构运维及安全项目"
NAME_ONE = "运维服务（第1包）"
NAME_TWO = "安全服务（第2包）"
HEADER = ["名称", "供应商名称", "规格型号", "单价", "数量"]
PACKAGE_ROWS = [
    [NAME_ONE, "甲公司", "详见附件", "详见附件", "详见附件"],
    [NAME_TWO, "乙公司", "详见附件", "详见附件", "详见附件"],
]


def model_rows():
    return [
        ExtractedEntity(product_name="原模型服务A", brand="甲公司", spec_model="SX-1",
                        unit_price="100元", quantity="1项"),
        ExtractedEntity(product_name="原模型服务B", brand="乙公司", spec_model="SX-2",
                        unit_price="200元", quantity="2项"),
    ]


def source(*, awards=None, rows=None, body_rows=None, serialized_rows=None, header=None,
           global_amount="", item_tail="", later_text="", metadata_project=PROJECT):
    awards = [("乙公司", "22000元"), ("甲公司", "11000元")] if awards is None else awards
    rows = PACKAGE_ROWS if rows is None else rows
    body_rows = rows if body_rows is None else body_rows
    serialized_rows = rows if serialized_rows is None else serialized_rows
    header = HEADER if header is None else header
    body = f"项目名称：{PROJECT}\n"
    if global_amount:
        body += global_amount + "\n"
    body += "三、中标信息\n"
    for supplier, amount in awards:
        body += f"供应商名称：{supplier}\n中标金额：{amount}\n"
    body += "四、主要标的信息\n"
    body += "\n".join(" | ".join(row) for row in [header, *body_rows]) + "\n"
    if item_tail:
        body += item_tail + "\n"
    body += "五、评审专家名单\n虚构专家\n" + later_text
    metadata = [["公告信息"], ["采购项目名称", metadata_project]]
    return build_summary_source(body, [metadata, [header, *serialized_rows]])


def clean(rows, text):
    return sanitize_summary_entities(rows, detail_status="no_attachment", source_text=text)


def dump(rows):
    return [row.model_dump() for row in rows]


def package_mapping(rows):
    return {row.product_name: (row.brand, row.total_price) for row in rows}


def test_reversed_award_supplier_order_still_matches_body_package_names_and_amounts():
    originals = model_rows()
    before = dump(originals)
    result = clean(originals, source())
    assert len(result) == 2
    assert package_mapping(result) == {
        "运维服务(第1包)": ("甲公司", "11000元"),
        "安全服务(第2包)": ("乙公司", "22000元"),
    }
    assert all(not row.spec_model and not row.unit_price and not row.quantity for row in result)
    assert dump(originals) == before


def test_attachment_filename_package_label_cannot_replace_unowned_model_summary_rows():
    originals = model_rows()
    before = dump(originals)
    text = source(rows=[]) + "\n\n--- 附件：第1包清单.txt ---\n附件中的设备明细\n"
    result = clean(originals, text)
    assert dump(result) == before
    assert dump(originals) == before


def test_reversed_model_summary_order_is_not_package_identity_evidence():
    originals = list(reversed(model_rows()))
    before = dump(originals)
    result = clean(originals, source())
    assert len(result) == 2
    assert package_mapping(result) == {
        "运维服务(第1包)": ("甲公司", "11000元"),
        "安全服务(第2包)": ("乙公司", "22000元"),
    }
    assert dump(originals) == before


@pytest.mark.parametrize("global_amount", [
    "总中标金额：33000元",
    "项目总成交金额：33000元",
])
def test_project_total_does_not_shift_or_replace_the_individual_package_awards(global_amount):
    result = clean(model_rows(), source(global_amount=global_amount))
    assert len(result) == 2
    assert package_mapping(result) == {
        "运维服务(第1包)": ("甲公司", "11000元"),
        "安全服务(第2包)": ("乙公司", "22000元"),
    }


def test_project_total_without_individual_award_amounts_is_not_a_package_award():
    originals = model_rows()
    before = dump(originals)
    text = source(awards=[("甲公司", "详见公告"), ("乙公司", "详见公告")],
                  global_amount="项目总中标金额：33000元")
    result = clean(originals, text)
    assert dump(result) == before
    assert dump(originals) == before


@pytest.mark.parametrize("later_text", [
    "六、代理服务收费标准及金额\n第1包代理服务费：330元\n第2包代理服务费：440元\n",
    "六、评审结果\n第1包评分：95分\n第2包评分：90分\n",
])
def test_fee_or_score_section_package_labels_are_not_main_item_owners(later_text):
    originals = model_rows()
    before = dump(originals)
    result = clean(originals, source(rows=[], later_text=later_text))
    assert dump(result) == before
    assert dump(originals) == before


def test_attachment_text_package_labels_do_not_establish_body_ownership():
    originals = model_rows()
    before = dump(originals)
    text = source(rows=[]) + (
        "\n\n--- 附件：虚构清单.txt ---\n"
        "运维服务（第1包）\n安全服务（第2包）\n"
    )
    assert dump(clean(originals, text)) == before
    assert dump(originals) == before


def test_same_supplier_multiple_packages_without_award_package_identity_defers():
    rows = [
        [NAME_ONE, "甲公司", "详见附件", "详见附件", "详见附件"],
        [NAME_TWO, "甲公司", "详见附件", "详见附件", "详见附件"],
    ]
    originals = model_rows()
    before = dump(originals)
    text = source(rows=rows, awards=[("甲公司", "11000元"), ("甲公司", "22000元")])
    assert dump(clean(originals, text)) == before
    assert dump(originals) == before


def test_conflicting_suppliers_for_the_same_main_package_do_not_generate_summary_rows():
    rows = [
        [NAME_ONE, "甲公司", "详见附件", "详见附件", "详见附件"],
        [NAME_ONE, "乙公司", "详见附件", "详见附件", "详见附件"],
    ]
    originals = model_rows()
    before = dump(originals)
    assert dump(clean(originals, source(rows=rows))) == before
    assert dump(originals) == before


@pytest.mark.parametrize("item_tail", [
    "说明：以上为候选人报价，非最终成交结果。",
    "1.说明：以上为候选人报价，非最终成交结果。",
])
def test_candidate_qualifier_prevents_collapsing_the_preceding_main_package_table(item_tail):
    originals = model_rows()
    before = dump(originals)
    assert dump(clean(originals, source(item_tail=item_tail))) == before
    assert dump(originals) == before


@pytest.mark.parametrize("body_rows,serialized_rows", [
    ([PACKAGE_ROWS[0]], PACKAGE_ROWS),
    (PACKAGE_ROWS, [PACKAGE_ROWS[0]]),
    ([PACKAGE_ROWS[0], PACKAGE_ROWS[1][:-1]], PACKAGE_ROWS),
    (PACKAGE_ROWS, [PACKAGE_ROWS[0], PACKAGE_ROWS[1][:-1]]),
    (PACKAGE_ROWS, [["另一个服务（第1包）", "甲公司", "详见附件", "详见附件", "详见附件"], PACKAGE_ROWS[1]]),
])
def test_missing_partial_or_mismatching_serialized_main_item_proof_keeps_original_rows(body_rows, serialized_rows):
    originals = model_rows()
    before = dump(originals)
    text = source(body_rows=body_rows, serialized_rows=serialized_rows)
    assert dump(clean(originals, text)) == before
    assert dump(originals) == before


def test_conflicting_project_metadata_prevents_using_the_serialized_package_table():
    originals = model_rows()
    before = dump(originals)
    assert dump(clean(originals, source(metadata_project="另一个虚构项目"))) == before
    assert dump(originals) == before


@pytest.mark.parametrize("with_serial_column", [True, False])
def test_true_individually_listed_items_keep_their_fields_instead_of_becoming_package_totals(with_serial_column):
    header = ["名称", "供应商名称", "规格型号", "单价", "数量"]
    rows = [
        ["网络设备A（第1包）", "甲公司", "NX-1", "1000元", "2台"],
        ["网络设备B（第2包）", "乙公司", "NX-2", "2000元", "3台"],
    ]
    originals = [
        ExtractedEntity(product_name="网络设备A（第1包）", brand="甲公司", spec_model="NX-1",
                        unit_price="1000元", quantity="2台"),
        ExtractedEntity(product_name="网络设备B（第2包）", brand="乙公司", spec_model="NX-2",
                        unit_price="2000元", quantity="3台"),
    ]
    before = dump(originals)
    if with_serial_column:
        header = ["序号", *header]
        rows = [[str(index), *row] for index, row in enumerate(rows, 1)]
    result = clean(originals, source(rows=rows, header=header))
    assert dump(result) == [before[0] | {"total_price": "2000元"}, before[1] | {"total_price": "6000元"}]
    assert dump(originals) == before


def test_details_entrypoint_keeps_attachment_items_and_source_metadata(monkeypatch):
    import json

    from extractor.llm_extractor import LLMExtractor

    extractor = object.__new__(LLMExtractor)
    response = json.dumps({"detail_entities": [
        {"package_name": "原附件包件", "product_name": "附件设备A", "category": "",
         "brand": "甲品牌", "spec_model": "AD-1", "unit_price": "100元", "quantity": "2台",
         "total_price": "200元", "source_page": "7"},
        {"package_name": "原附件包件", "product_name": "附件设备B", "category": "",
         "brand": "乙品牌", "spec_model": "BD-2", "unit_price": "300元", "quantity": "1台",
         "total_price": "300元", "source_page": "8"},
    ]}, ensure_ascii=False)
    # Substitute only external inference; real detail parsing/cleanup stays.
    monkeypatch.setattr(extractor, "_call_llm", lambda *args: response)
    rows, status, error = extractor.extract_details(texts={
        "第1包明细.txt": "名称 | 品牌 | 规格型号 | 单价 | 数量\n"
                         "附件设备A | 甲品牌 | AD-1 | 100元 | 2台\n"
                         "附件设备B | 乙品牌 | BD-2 | 300元 | 1台",
    }, tables={})
    assert status == "available" and error == ""
    assert dump(rows) == [
        {"product_name": "附件设备A", "category": "", "brand": "甲品牌", "spec_model": "AD-1",
         "unit_price": "100元", "quantity": "2台", "total_price": "200元",
         "package_name": "原附件包件", "source_file": "第1包明细.txt", "source_page": "7"},
        {"product_name": "附件设备B", "category": "", "brand": "乙品牌", "spec_model": "BD-2",
         "unit_price": "300元", "quantity": "1台", "total_price": "300元",
         "package_name": "原附件包件", "source_file": "第1包明细.txt", "source_page": "8"},
    ]


@pytest.mark.parametrize("change", ["partial_main", "scope", "ambiguous_package", "detached_award"])
def test_explicit_award_blocks_do_not_override_conflicting_or_untrusted_main_evidence(change):
    text = source().replace("供应商名称：乙公司", "第2包\n供应商名称：乙公司").replace(
        "供应商名称：甲公司", "第1包\n供应商名称：甲公司")
    if change == "partial_main":
        text = text.replace(" | ".join(PACKAGE_ROWS[1]), " | ".join(PACKAGE_ROWS[1][:-1]), 1)
    elif change == "scope":
        text = text.replace("--- HTML表格1 ---", "--- UNKNOWN ---\n--- HTML表格1 ---")
    elif change == "ambiguous_package":
        text = source().replace("三、中标信息\n", "三、中标信息\n第1包、第2包\n")
    else:
        text += "\n--- HTML表格3 ---\n供应商名称 | 中标金额\n乙公司 | 9999元"
    originals = model_rows()
    assert dump(clean(originals, text)) == dump(originals)


@pytest.mark.parametrize("placement", ["inside", "outside"])
def test_explicit_awards_do_not_bypass_a_disqualified_main_item_region(placement):
    qualifier = "说明：主要标的信息为候选人报价，非最终成交结果。"
    text = source(item_tail=qualifier if placement == "inside" else "",
                  later_text="六、其他补充事宜\n" + qualifier if placement == "outside" else "")
    text = text.replace("供应商名称：乙公司", "第2包\n供应商名称：乙公司").replace(
        "供应商名称：甲公司", "第1包\n供应商名称：甲公司")
    originals = model_rows()
    assert dump(clean(originals, text)) == dump(originals)


@pytest.mark.parametrize("column,values", [
    ("采购包", ["2", "1"]), ("包号", ["第2包", "第1包"]),
    ("合同包", ["未知", "2"]), ("采购包", ["1", ""]),
])
def test_package_column_contradiction_or_unknown_identity_is_not_discarded(column, values):
    text = source(header=[*HEADER, column], rows=[[*row, value] for row, value in zip(PACKAGE_ROWS, values)])
    originals = model_rows()
    assert dump(clean(originals, text)) == dump(originals)


def test_matching_package_column_is_a_consistent_second_identity_witness():
    text = source(header=[*HEADER, "采购包"], rows=[[*row, str(i)] for i, row in enumerate(PACKAGE_ROWS, 1)])
    assert package_mapping(clean(model_rows(), text)) == {
        "运维服务(第1包)": ("甲公司", "11000元"),
        "安全服务(第2包)": ("乙公司", "22000元"),
    }


@pytest.mark.parametrize("identities", [["第2包", "第1包"], ["未知", "第2包"]])
def test_award_matrix_package_column_cannot_disappear_behind_generic_supplier_blocks(identities):
    table = "采购包 | 供应商名称 | 中标金额\n" + "\n".join([
        f"{identities[0]} | 甲公司 | 11000元", f"{identities[1]} | 乙公司 | 22000元",
    ])
    text = source().replace("四、主要标的信息", table + "\n四、主要标的信息")
    text += "\n--- HTML表格3 ---\n" + table
    originals = model_rows()
    assert dump(clean(originals, text)) == dump(originals)


@pytest.mark.parametrize("declaration", [
    "中标人：丙公司", "成交供应商：丙公司", "中标人\n丙公司",
])
def test_unparsed_award_owner_declarations_veto_uniqueness(declaration):
    text = source().replace("中标金额：11000元", "中标金额：11000元\n" + declaration)
    originals = model_rows()
    assert dump(clean(originals, text)) == dump(originals)


@pytest.mark.parametrize("table", [
    "采购包 | 中标人\n第1包 | 丙公司",
    "公告信息\n备注 | 本包中标人为丙公司",
])
def test_unknown_serialized_award_owners_are_not_ignored(table):
    text = source() + "\n--- HTML表格3 ---\n" + table
    originals = model_rows()
    assert dump(clean(originals, text)) == dump(originals)


@pytest.mark.parametrize("partial", [
    "其他服务（第1包） | 丙公司", "其他服务（第1包） | 甲公司",
    "其他服务（第1包）\n丙公司", "第1包\n供应商名称：丙公司",
])
def test_unparsed_competing_main_package_claims_with_different_names_veto_collapse(partial):
    originals = model_rows()
    assert dump(clean(originals, source(item_tail=partial))) == dump(originals)
