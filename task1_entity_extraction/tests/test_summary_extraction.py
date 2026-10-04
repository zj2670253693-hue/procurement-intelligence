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

    assert "每个采购包只输出一条" in system_prompt
    assert "中标或成交供应商" in system_prompt
    assert "不得" in system_prompt and "预算" in system_prompt
    assert "采购包一成交供应商为虚构供应商" in user_prompt


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
