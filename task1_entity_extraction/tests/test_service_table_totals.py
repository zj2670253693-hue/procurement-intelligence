"""Explicit service totals: reject shifted rows and unowned/competing awards.

All examples are fictional. Expected prices are literals, not proof-helper output.
"""
from copy import deepcopy
import pytest
from extractor.summary_rules import build_summary_source, sanitize_summary_entities
from models.schemas import ExtractedEntity

PROJECT = "演示信息化维保项目"
HEADER = ["序号", "标的名称", "品目名称", "数量",
          "单价(元)/费率（%）/折扣（折）", "总价(元)",
          "是否中小企业", "中小企业（中型/小型/微型）",
          "服务范围", "服务要求", "服务时间", "服务标准", "优惠产品简要描述"]
AWARD_HEADER = ["供应商名称", "供应商地址", "供应商联系电话", "中标（成交）金额(元)"]
ITEMS = [
    ["", PROJECT + "（二标段）", "其他信息技术服务", "1", "37400", "37400",
     "是", "小型企业", "详见招标文件", "详见招标文件", "一年", "详见招标文件", "无"],
    ["", PROJECT + "（三标段）", "其他信息技术服务", "1", "16800", "16800",
     "是", "微型企业", "详见招标文件", "详见招标文件", "一年", "详见招标文件", "无"],
]
AWARDS = [
    ["样例甲公司（二标段）", "演示路1号", "00000000001", "37400"],
    ["样例乙公司（三标段）", "演示路2号", "00000000002", "16800"],
]


def original(index=0, **changes):
    return ExtractedEntity(**(dict(product_name=ITEMS[index][1], category="其他信息技术服务",
        brand=["样例甲公司", "样例乙公司"][index], spec_model="", unit_price=ITEMS[index][4] + "元",
        quantity="1", total_price=["37400元", "16800元"][index]) | changes))


def source(*, items=None, body_items=None, awards=None, body_awards=None,
           title="服务类", header=None, award_header=None, extra_tables=None,
           item_tail="", after_boundary="", project=PROJECT, announcement_title=""):
    items = deepcopy(ITEMS if items is None else items)
    awards = deepcopy(AWARDS if awards is None else awards)
    body_items = items if body_items is None else body_items
    body_awards = awards if body_awards is None else body_awards
    header = HEADER if header is None else header
    award_header = AWARD_HEADER if award_header is None else award_header
    def vertical(rows):
        return "\n".join(c for row in rows for c in row if c)
    body = (announcement_title + f"\n项目名称：{PROJECT}\n三、中标（成交）信息\n"
            + vertical([award_header, *body_awards])
            + "\n四、主要标的信息\n" + title + "\n"
            + vertical([header, *body_items]) + "\n" + item_tail
            + "\n五、评审得分排名\n供应商名称\n得分\n评审报价\n其他公司\n80\n1000"
            + "\n六、评审专家名单\n演示专家\n" + after_boundary)
    tables = [[["公告信息"], ["采购项目名称", project]],
              [award_header, *awards], [[title], header, *items], *(extra_tables or [])]
    return build_summary_source(body, tables)


def clean(rows, text):
    return sanitize_summary_entities(rows, detail_status="no_attachment", source_text=text)


@pytest.mark.parametrize("index,total", [(0, "37400元"), (1, "16800元")])
def test_service_title_empty_serial_and_separate_award_preserve_explicit_existing_total(index, total):
    row = original(index)
    before = row.model_dump()
    result = clean([row], source())
    assert result[0].total_price == total
    assert result[0].model_dump() == before
    assert row.model_dump() == before


def test_award_row_order_is_not_the_identity_key():
    assert [r.total_price for r in clean([original(), original(1)], source(awards=list(reversed(AWARDS))))] == ["37400元", "16800元"]


@pytest.mark.parametrize("total", ["37,400.00元", "3.74万元"])
def test_service_restore_preserves_equivalent_cached_spelling(total):
    assert clean([original(total_price=total)], source())[0].total_price == total


@pytest.mark.parametrize("changes", [
    {"brand": "错误公司"}, {"brand": ""}, {"total_price": "37500元"},
    {"total_price": ""}, {"product_name": PROJECT + "（四标段）"}, {"spec_model": "未被原文证明的型号ZX9"},
])
def test_identity_conflicts_defer_but_unique_literal_can_correct_missing_owner_or_amount(changes):
    expected = "37400元" if changes in ({"brand": ""}, {"total_price": "37500元"}, {"total_price": ""}) else ""
    assert clean([original(**changes)], source())[0].total_price == expected


@pytest.mark.parametrize("kind", ["duplicate_item", "duplicate_award", "conflicting_award",
    "incomplete_award", "incomplete_item", "different_body_item", "different_body_award",
    "detached_item", "unknown_title", "budget", "candidate", "wrong_project",
    "missing_source_currency", "mixed_package", "unparsed_item", "table_disclaimer"])
def test_service_table_ambiguity_and_negative_evidence_fail_closed(kind):
    options = {}
    if kind == "duplicate_item": options["items"] = [*ITEMS, ITEMS[0]]
    elif kind == "duplicate_award": options["awards"] = [*AWARDS, AWARDS[0]]
    elif kind == "conflicting_award": options["awards"] = [*AWARDS, ["另一公司（二标段）", "演示路3号", "00000000003", "37400"]]
    elif kind == "incomplete_award":
        values = deepcopy(AWARDS); values.append(["另一公司（二标段）", "", "", ""]); options["awards"] = values
    elif kind == "incomplete_item":
        values = deepcopy(ITEMS); values[0][9] = ""; options["items"] = values
    elif kind == "different_body_item":
        values = deepcopy(ITEMS); values[0][5] = "37500"; options["body_items"] = values
    elif kind == "different_body_award":
        values = deepcopy(AWARDS); values[0][0] = "另一公司（二标段）"; options["body_awards"] = values
    elif kind == "detached_item": options["extra_tables"] = [[["服务类"], HEADER, ITEMS[0]]]
    elif kind == "unknown_title": options["title"] = "未知服务清单"
    elif kind == "budget": options["header"] = [*HEADER[:5], "预算总价(元)", *HEADER[6:]]
    elif kind == "candidate": options["announcement_title"] = "候选人公示"
    elif kind == "wrong_project": options["project"] = "不同项目"
    elif kind == "missing_source_currency":
        options["header"] = [*HEADER[:5], "总价", *HEADER[6:]]
        options["award_header"] = [*AWARD_HEADER[:3], "中标（成交）金额"]
    elif kind == "mixed_package":
        values = deepcopy(AWARDS); values[0][0] = "样例甲公司（二标段）（三标段）"; options["awards"] = values
    elif kind == "unparsed_item": options["item_tail"] = "标的名称：" + ITEMS[0][1]
    elif kind == "table_disclaimer": options["after_boundary"] = "上述主要标的信息表为候选人报价，并非成交金额"
    assert clean([original()], source(**options))[0].total_price == ""


def test_missing_original_total_uses_source_literal_not_the_mixed_rate_column():
    assert clean([original(total_price="")], source())[0].total_price == "37400元"


def test_duplicate_original_names_remain_unrestored():
    rows = [original(), original(spec_model="不同规格")]
    assert all(r.total_price == "" for r in clean(rows, source()))


def test_multiple_item_names_in_same_literal_lot_do_not_copy_whole_package_award():
    second = deepcopy(ITEMS[0]); second[1] = "另一个服务项目（二标段）"
    assert clean([original()], source(items=[*ITEMS, second]))[0].total_price == ""


def test_conflicting_unparsed_package_context_cannot_be_hidden_by_service_projection():
    text = source().replace("服务类\n序号", "采购包4\n服务类\n序号", 1)
    assert clean([original()], text)[0].total_price == ""


@pytest.mark.parametrize("disclaimer", ["上述主要标的信息表并非成交金额", "本表不是最终中标金额", "上述金额非成交价"])
def test_explicit_negative_award_disclaimer_after_boundary_remains_authoritative(disclaimer):
    assert clean([original()], source(after_boundary=disclaimer))[0].total_price == ""


def test_unknown_titled_serialized_competitor_cannot_disappear():
    competing = deepcopy(ITEMS[0]); competing[5] = "37500"
    text = source(extra_tables=[[["未知服务清单"], HEADER, competing]])
    assert clean([original()], text)[0].total_price == ""
