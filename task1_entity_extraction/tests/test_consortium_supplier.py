"""Literal, same-package consortium suffix repair; never price/order matching."""
import pytest

from extractor.summary_rules import build_summary_source, sanitize_summary_entities
from models.schemas import ExtractedEntity


NAME = "虚构平台服务"
LEAD = "样例科技（虚构市）有限公司"
MEMBER = "样例研究院有限公司"
FULL = LEAD + "(联合体成员：" + MEMBER + ")"
OTHER = "另一个样例公司"
AWARD = ["供应商名称", "供应商地址", "中标（成交）金额"]
ITEM = ["品目号", "品目名称", "采购标的", "服务范围", "服务要求", "服务时间", "服务标准", "金额(元)"]
AWARD_ROW = [FULL, "虚构地址", "10000元"]
ITEM_ROW = ["1-1", "基础软件开发服务", NAME, "项目范围", "项目要求", "12个月", "技术标准", "10000"]


def model(**changes):
    return ExtractedEntity(**(dict(product_name=NAME, category="基础软件开发服务", brand=LEAD) | changes))


def source(*, layout="vertical", full=FULL, owner=None, award_package="合同包1(基础软件开发服务):",
           item_package="合同包1(基础软件开发服务):", award_header=None, item_header=None,
           award_row=None, item_row=None, tail="", award_title="三、采购结果",
           item_title="四、主要标的信息", extra_tables=None, body_full=None,
           body_item_row=None, body_owner=None, project="虚构平台采购"):
    award_header = AWARD if award_header is None else award_header
    item_header = ITEM if item_header is None else item_header
    award_row = [full, *AWARD_ROW[1:]] if award_row is None else award_row
    item_row = ITEM_ROW if item_row is None else item_row
    body_award_row = award_row.copy()
    if body_full is not None:
        body_award_row[0] = body_full
    body_item_row = item_row if body_item_row is None else body_item_row
    owner = f"服务类（{LEAD}，联合体成员：{MEMBER}）" if owner is None else owner
    owner = owner if body_owner is None else body_owner
    serialize = (lambda row: " | ".join(row)) if layout == "pipe" else (lambda row: "\n".join(row))
    body = (f"二、项目名称：{project}\n{award_title}\n{award_package}\n"
            + "\n".join(serialize(row) for row in [award_header, body_award_row])
            + f"\n{item_title}\n{item_package}\n{owner}\n"
            + "\n".join(serialize(row) for row in [item_header, body_item_row])
            + f"\n{tail}\n五、评审专家名单\n虚构专家")
    return build_summary_source(body, [[["公告信息"], ["采购项目名称", project]],
                                     [award_header, award_row], [item_header, item_row], *(extra_tables or [])])


def clean(rows, text):
    return sanitize_summary_entities(rows, detail_status="no_attachment", source_text=text)


@pytest.mark.parametrize("layout", ["pipe", "vertical"])
def test_exact_existing_lead_gets_complete_literal_same_package_award_name(layout):
    originals = [model()]
    snapshot = [row.model_dump() for row in originals]
    result = clean(originals, source(layout=layout))
    assert result[0].brand == FULL
    assert result[0].model_dump(exclude={"brand"}) == {key: value for key, value in snapshot[0].items() if key != "brand"}
    assert [row.model_dump() for row in originals] == snapshot


def test_other_rows_count_order_and_all_nonbrand_fields_remain_unchanged():
    originals = [model(product_name="另一个项目", brand=OTHER), model(), model(product_name="末尾项目", brand=OTHER)]
    result = clean(originals, source())
    assert [row.product_name for row in result] == ["另一个项目", NAME, "末尾项目"]
    assert [row.brand for row in result] == [OTHER, FULL, OTHER]
    assert [row.model_dump(exclude={"brand"}) for row in result] == [row.model_dump(exclude={"brand"}) for row in originals]


def test_nfkc_space_equivalent_existing_lead_returns_original_source_spelling_not_composed_name():
    literal = "样例科技(虚构市)有限公司（联合体成员：样例研究院有限公司）"
    owner = "服务类（样例科技(虚构市)有限公司，联合体成员：样例研究院有限公司）"
    assert clean([model(brand="样例科技 （虚构市） 有限公司")], source(full=literal, owner=owner))[0].brand == literal


@pytest.mark.parametrize("brand", [FULL, "真实产品品牌", "样例科技", "样例科技有限公司", "", "制造商：" + LEAD])
def test_full_name_brand_alias_empty_or_label_is_not_an_exact_existing_supplier_lead(brand):
    originals = [model(brand=brand)]
    # Empty/label input may be subject to existing rules; the new suffix pass
    # must not manufacture a consortium from such a non-lead value.
    assert clean(originals, source())[0].brand != FULL if brand != FULL else clean(originals, source())[0].brand == FULL


@pytest.mark.parametrize("owner", [f"服务类（{LEAD}）", f"服务类（{LEAD}，联合体成员：第三方公司）",
                                 f"货物类（{LEAD}，联合体成员：{MEMBER}）", "", "未知供应商类型"])
def test_missing_conflicting_or_goods_owner_is_not_unique_service_supplier_evidence(owner):
    assert clean([model()], source(owner=owner))[0].brand == LEAD


@pytest.mark.parametrize("full", [LEAD, LEAD + "(分公司：" + MEMBER + ")",
                                LEAD + "(联合体成员：)", LEAD + "(联合体成员：" + MEMBER,
                                LEAD + "(联合体成员：" + MEMBER + ")额外说明"])
def test_only_a_complete_literal_consortium_suffix_can_be_restored(full):
    assert clean([model()], source(full=full))[0].brand == LEAD


@pytest.mark.parametrize("changes", [dict(award_package="合同包2:"), dict(item_package="合同包2:"),
                                   dict(award_package=""), dict(item_package=""),
                                   dict(item_package="所属包：未知"), dict(award_title="三、候选人名单"),
                                   dict(award_title="三、评审结果"), dict(item_title="四、采购需求")])
def test_unknown_package_mismatched_package_or_unawarded_role_defers(changes):
    assert clean([model()], source(**changes))[0].brand == LEAD


@pytest.mark.parametrize("side", ["body_supplier", "body_item", "table_item"])
def test_body_and_html_must_agree_on_complete_supplier_and_item_rows(side):
    kwargs = {}
    if side == "body_supplier":
        kwargs["body_full"] = LEAD + "(联合体成员：第三方公司)"
    else:
        row = ITEM_ROW.copy()
        row[3] = "另一服务范围"
        kwargs["body_item_row" if side == "body_item" else "item_row"] = row
        if side == "table_item":
            kwargs["body_item_row"] = ITEM_ROW
    assert clean([model()], source(**kwargs))[0].brand == LEAD


def test_item_identity_is_not_selected_by_its_price_or_supplier_alone():
    assert clean([model(product_name="另一个标的")], source())[0].brand == LEAD


def test_final_pass_rejects_conflicting_current_category_without_price_matching():
    # The old summary can normalize a raw category before this final pass.
    # Exercise the new guard with the current post-cleaning entity directly.
    from extractor.consortium_supplier import restore_consortium_suppliers
    assert restore_consortium_suppliers([model(category="另一个标的")], source_text=source())[0].brand == LEAD


def test_unknown_source_marker_or_project_scope_conflict_defers():
    for text in [source().replace("--- HTML表格2 ---", "--- 未知来源2 ---"),
                 source().replace("采购项目名称 | 虚构平台采购", "采购项目名称 | 另一个项目")]:
        assert clean([model()], text)[0].brand == LEAD


def test_member_claim_outside_complete_item_scope_is_not_ignored():
    assert clean([model()], source(tail="联合体成员：第三方公司"))[0].brand == LEAD


def test_candidate_negative_declaration_in_item_block_remains_a_veto():
    assert clean([model()], source(tail="此处为候选人投标报价"))[0].brand == LEAD


def test_unknown_item_header_cannot_hide_another_owner_column():
    header = ITEM.copy()
    header[3] = "未知供应商上下文"
    assert clean([model()], source(item_header=header))[0].brand == LEAD


def test_empty_item_cell_is_not_complete_row_evidence():
    row = ITEM_ROW.copy()
    row[3] = ""
    assert clean([model()], source(item_row=row))[0].brand == LEAD


def test_repeated_serialized_table_alone_cannot_invent_another_package():
    assert clean([model()], source(extra_tables=[[AWARD, AWARD_ROW], [ITEM, ITEM_ROW]]))[0].brand == FULL


def test_attachment_only_supplier_and_item_are_not_summary_proof():
    assert clean([model()], "公告正文\n\n--- 附件：联合体说明.txt ---\n" + source())[0].brand == LEAD


def test_conflicting_known_owner_without_a_parsed_item_row_still_vetoes():
    assert clean([model()], source(tail=f"服务类（{LEAD}，联合体成员：第三方公司）"))[0].brand == LEAD


def test_unparsed_competing_award_supplier_declaration_cannot_disappear():
    text = source().replace("\n四、主要标的信息", f"\n供应商名称：{LEAD}(联合体成员：第三方公司)\n四、主要标的信息")
    assert clean([model()], text)[0].brand == LEAD


def test_unmatched_competing_serialized_supplier_row_is_not_ignored():
    competing = [LEAD + "(联合体成员：第三方公司)", "虚构地址", "10000元"]
    assert clean([model()], source(extra_tables=[[AWARD, competing]]))[0].brand == LEAD


def test_second_awarded_package_with_same_lead_prevents_price_based_selection():
    competing = [LEAD + "(联合体成员：第三方公司)", "虚构地址", "10000元"]
    text = source(extra_tables=[[AWARD, competing]]).replace("\n四、主要标的信息", "\n合同包2:\n" + "\n".join(AWARD + competing) + "\n四、主要标的信息")
    assert clean([model()], text)[0].brand == LEAD


def test_augmentation_that_would_create_a_db_dedup_collision_defers():
    from extractor.consortium_supplier import restore_consortium_suppliers
    originals = [model(), model(brand=FULL)]
    result = restore_consortium_suppliers(originals, source_text=source())
    assert [row.model_dump() for row in result] == [row.model_dump() for row in originals]


def test_multiple_current_same_item_lead_rows_cannot_be_selected_by_other_prices():
    from extractor.consortium_supplier import restore_consortium_suppliers
    originals = [model(unit_price="100元"), model(unit_price="200元")]
    assert [row.brand for row in restore_consortium_suppliers(originals, source_text=source())] == [LEAD, LEAD]


@pytest.mark.parametrize("side", ["body", "table"])
def test_unmatched_competing_same_item_row_is_not_ignored(side):
    changed = ITEM_ROW.copy()
    changed[3] = "另一服务范围"
    text = source(extra_tables=[[ITEM, changed]]) if side == "table" else source(tail="\n".join(changed))
    assert clean([model()], text)[0].brand == LEAD


@pytest.mark.parametrize("claim", ["采购标的：" + NAME + "；供应商名称：第三方公司", NAME, "未知标的声明：" + NAME])
def test_unparsed_same_name_declaration_is_not_dropped(claim):
    assert clean([model()], source(tail=claim))[0].brand == LEAD


@pytest.mark.parametrize("role", ["award", "item"])
def test_unparsed_package_ownership_cannot_inherit_previous_known_package(role):
    text = source()
    if role == "item":
        text = text.replace("服务类（", "所属包：合同包2\n服务类（", 1)
    else:
        text = text.replace("供应商名称\n", "所属包：合同包2\n供应商名称\n", 1)
    assert clean([model()], text)[0].brand == LEAD


@pytest.mark.parametrize("title", ["虚构平台采购候选人公示", "虚构平台采购候选人公告"])
def test_announcement_candidate_title_vetoes_apparently_awarded_sections(title):
    assert clean([model()], title + "\n" + source())[0].brand == LEAD


def test_cross_section_disclaimer_referring_to_main_item_table_remains_authoritative():
    text = source().replace("\n\n--- HTML表格1 ---", "\n六、其他补充事宜\n上述主要标的信息仅为候选人信息\n\n--- HTML表格1 ---")
    assert clean([model()], text)[0].brand == LEAD


def test_unrelated_later_competitor_table_is_not_a_main_item_disclaimer():
    text = source().replace("\n\n--- HTML表格1 ---", "\n六、其他补充事宜\n其他候选人的评审报价\n\n--- HTML表格1 ---")
    assert clean([model()], text)[0].brand == FULL


@pytest.mark.parametrize("second_name_header", ["标的名称", "服务名称"])
def test_ambiguous_multiple_name_columns_are_negative_not_silently_ignored(second_name_header):
    header, row = ITEM.copy(), ITEM_ROW.copy()
    header[3], row[3] = second_name_header, "另一服务范围"
    assert clean([model()], source(extra_tables=[[header, row]]))[0].brand == LEAD
