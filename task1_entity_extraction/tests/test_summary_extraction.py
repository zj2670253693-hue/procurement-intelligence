import json

import pytest

from extractor.llm_extractor import LLMExtractor
from extractor.prompts import build_summary_prompt
from extractor.summary_rules import build_summary_source, sanitize_summary_entities
from models.schemas import ExtractedEntity


def test_summary_source_contains_html_tables_without_attachment_content():
    source = build_summary_source(
        "公告正文",
        [[
            ["采购包", "成交供应商"],
            ["采购包一", "虚构供应商"],
        ]],
    )

    assert "公告正文" in source
    assert "采购包 | 成交供应商" in source
    assert "采购包一 | 虚构供应商" in source
    assert "附件设备甲" not in source


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("missing", "附件缺失"),
        ("unreadable", "附件缺失"),
        ("available", ""),
        ("no_attachment", ""),
    ],
)
def test_reference_placeholder_follows_attachment_status(status, expected):
    result = sanitize_summary_entities(
        [ExtractedEntity(product_name="采购包一", spec_model="详见附件")],
        detail_status=status,
        source_text="规格型号详见附件",
    )

    assert result[0].spec_model == expected


def test_non_attachment_reference_is_cleared_instead_of_marked_missing():
    result = sanitize_summary_entities(
        [
            ExtractedEntity(
                product_name="采购包一",
                spec_model="按招标要求提供",
                brand="详见附件",
            )
        ],
        detail_status="missing",
        source_text="品牌详见附件，规格按招标要求提供",
    )

    assert result[0].spec_model == ""
    assert result[0].brand == ""


def test_budget_times_discount_is_not_accepted_as_transaction_total():
    result = sanitize_summary_entities(
        [ExtractedEntity(product_name="采购包一", total_price="4.68万元")],
        detail_status="no_attachment",
        source_text="项目预算6万元，成交方式为市场指导价的78%",
    )

    assert result[0].total_price == ""


def test_budget_amount_itself_is_not_accepted_as_transaction_total():
    result = sanitize_summary_entities(
        [ExtractedEntity(product_name="采购包一", total_price="6万元")],
        detail_status="no_attachment",
        source_text="项目预算6万元，成交方式为市场指导价的78%",
    )

    assert result[0].total_price == ""


def test_explicit_transaction_amount_is_preserved():
    result = sanitize_summary_entities(
        [ExtractedEntity(product_name="采购包一", total_price="4.68万元")],
        detail_status="no_attachment",
        source_text="采购包一成交金额：4.68万元。项目预算为6万元。",
    )

    assert result[0].total_price == "4.68万元"


def test_duplicate_summary_rows_are_stably_deduplicated():
    rows = [
        ExtractedEntity(product_name="采购包一", brand="虚构供应商"),
        ExtractedEntity(product_name="采购包一", brand="虚构供应商"),
    ]

    result = sanitize_summary_entities(
        rows,
        detail_status="no_attachment",
        source_text="采购包一成交供应商为虚构供应商",
    )

    assert len(result) == 1


def test_summary_prompt_requires_package_granularity_and_supplier_brand():
    system_prompt, user_prompt = build_summary_prompt(
        "采购包一成交供应商为虚构供应商",
        has_tables=True,
        detail_status="no_attachment",
    )

    assert "每条标的物输出一条" in system_prompt
    assert "中标或成交供应商" in system_prompt
    assert "不得" in system_prompt and "预算" in system_prompt
    assert "采购包一成交供应商为虚构供应商" in user_prompt


def test_announcement_level_total_amount_is_recognized_as_transaction_amount():
    result = sanitize_summary_entities(
        [ExtractedEntity(product_name="虚构项目", total_price="507.4843万元")],
        detail_status="missing",
        source_text="总中标金额 | ￥507.484300 万元（人民币）",
    )

    assert result[0].total_price == "507.4843万元"


def test_per_supplier_winning_amount_is_recognized_as_transaction_amount():
    source_text = (
        "三、中标（成交）信息\n"
        "供应商名称：虚构供应商甲\n"
        "中标（成交）金额：337.3570000（万元）\n"
        "供应商名称：虚构供应商乙\n"
        "中标（成交）金额：170.1273000（万元）\n"
    )
    result = sanitize_summary_entities(
        [
            ExtractedEntity(product_name="虚构项目（第1包）", total_price="337.3570000（万元）"),
            ExtractedEntity(product_name="虚构项目（第2包）", total_price="170.1273000（万元）"),
        ],
        detail_status="missing",
        source_text=source_text,
    )

    assert [row.total_price for row in result] == ["337.3570000（万元）", "170.1273000（万元）"]


PACKAGE_ONLY_SOURCE = (
    "二、项目名称：虚构信息化设备采购项目\n"
    "三、中标（成交）信息\n"
    "供应商名称：虚构供应商甲\n"
    "中标（成交）金额：337.3570000（万元）\n"
    "供应商名称：虚构供应商乙\n"
    "中标（成交）金额：170.1273000（万元）\n"
    "四、主要标的信息\n"
    "1 | 虚构供应商甲 | 虚构信息化设备采购项目（第1包）详见附件 | 详见附件 | 详见附件 | 详见附件 | 详见附件\n"
    "2 | 虚构供应商乙 | 虚构信息化设备采购项目（第2包）详见附件 | 详见附件 | 详见附件 | 详见附件 | 详见附件\n"
)


def test_package_only_announcement_collapses_expanded_detail_rows():
    """公告只给包级信息时，附件展开出的设备明细不能当成基础层标的物。"""
    expanded = [
        ExtractedEntity(product_name=f"虚构设备{i}", brand="虚构品牌", unit_price="1000元", quantity="1台")
        for i in range(1, 6)
    ] + [
        ExtractedEntity(
            product_name="虚构信息化设备采购项目（第1包）",
            brand="虚构供应商甲",
            total_price="337.3570000（万元）",
        ),
        ExtractedEntity(
            product_name="虚构信息化设备采购项目（第2包）",
            brand="虚构供应商乙",
            total_price="170.1273000（万元）",
        ),
    ]

    result = sanitize_summary_entities(
        expanded,
        detail_status="available",
        source_text=PACKAGE_ONLY_SOURCE,
    )

    assert len(result) == 2
    assert all(not row.product_name.startswith("虚构设备") for row in result)
    assert all("第1包" in row.product_name or "第2包" in row.product_name for row in result)
    assert sorted(row.total_price for row in result) == ["170.1273000(万元)", "337.3570000(万元)"]
    assert sorted(row.brand for row in result) == ["虚构供应商乙", "虚构供应商甲"]


def test_announcement_listing_items_is_not_collapsed():
    """正文自己逐条列出标的物时，多行必须原样保留。"""
    source_text = (
        "四、主要标的信息\n"
        "1 | 智慧展示系统 | 1套 | 148000.00元\n"
        "2 | 智能讲台 | 2台 | 8000.00元\n"
        "3 | 智能设备集中控制终端 | 1台 | 4000.00元\n"
        "总中标金额：￥200.743900 万元（人民币）\n"
    )
    rows = [
        ExtractedEntity(product_name="智慧展示系统", unit_price="148000.00元", quantity="1套"),
        ExtractedEntity(product_name="智能讲台", unit_price="8000.00元", quantity="2台"),
        ExtractedEntity(product_name="智能设备集中控制终端", unit_price="4000.00元", quantity="1台"),
    ]

    result = sanitize_summary_entities(
        rows,
        detail_status="available",
        source_text=source_text,
    )

    assert len(result) == 3
    assert [row.product_name for row in result] == [
        "智慧展示系统",
        "智能讲台",
        "智能设备集中控制终端",
    ]


def test_extract_summary_applies_deterministic_cleanup(monkeypatch):
    extractor = object.__new__(LLMExtractor)
    response = {
        "entities": [
            {
                "product_name": "采购包一",
                "brand": "虚构供应商",
                "spec_model": "详见附件",
                "total_price": "4.68万元",
            }
        ]
    }
    monkeypatch.setattr(
        extractor,
        "_call_llm",
        lambda system_prompt, user_prompt: json.dumps(response, ensure_ascii=False),
    )

    result = extractor.extract_summary(
        "采购包一成交供应商为虚构供应商，成交金额4.68万元，规格详见附件",
        has_tables=False,
        detail_status="missing",
        announcement_id="fictional-001",
    )

    assert result.success is True
    assert result.detail_status == "missing"
    assert len(result.entities) == 1
    assert result.entities[0].spec_model == "附件缺失"
    assert result.entities[0].total_price == "4.68万元"
