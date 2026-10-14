"""Catch invented categories without changing other fields or entity granularity."""

import json

import pytest

from extractor.llm_extractor import LLMExtractor
from extractor.summary_rules import sanitize_summary_entities
from models.schemas import ExtractedEntity


def _extract(monkeypatch, text, category):
    extractor = object.__new__(LLMExtractor)
    response = {"entities": [{
        "product_name": "虚构平台建设服务", "category": category,
        "brand": "虚构供应商", "quantity": "1项", "total_price": "10万元",
    }]}
    monkeypatch.setattr(extractor, "_call_llm", lambda *args: json.dumps(response))
    return extractor.extract(text, announcement_id="fictional-category")


def test_legacy_rejects_category_invented_from_project_name(monkeypatch):
    result = _extract(monkeypatch, "品目 | \n名称 | 虚构平台建设服务", "C0201 软件开发服务")
    assert result.success is True
    assert result.entities[0].model_dump() == {
        "product_name": "虚构平台建设服务", "category": "",
        "brand": "虚构供应商", "spec_model": "", "unit_price": "",
        "quantity": "1项", "total_price": "10万元",
    }


@pytest.mark.parametrize("category,source", [
    ("服务/其他服务", "品目 | 服务/其他服务"),
    ("其他商业保险服务", "品目名称 | 采购标的\n其他商业保险服务 | 团体保险"),
    ("C17010200 网络接入服务", "品目编号 | 品目名称\nC17010200 | 网络接入服务"),
    ("软件和信息技术服务业", "--- 附件：明细 ---\n所属行业：软件和信息技术服务业"),
    ("C17010200 网络接入服务", "品目编号 | 品目名称\nＣ１７０１０２００ | 网络接入服务"),
    ("服务/其他服务", "品目：服 务/其 他\n服 务"),
])
def test_legacy_keeps_categories_present_in_html_or_attachment(monkeypatch, category, source):
    assert _extract(monkeypatch, source, category).entities[0].category == category


def test_invented_code_is_rejected_even_if_category_name_exists(monkeypatch):
    result = _extract(monkeypatch, "品目名称：软件开发服务", "C0201 软件开发服务")
    assert result.entities[0].category == ""


def test_empty_category_is_not_auto_filled(monkeypatch):
    assert _extract(monkeypatch, "品目：其他服务", "").entities[0].category == ""


@pytest.mark.parametrize("category,source", [
    ("货物类", "六、采购方式:\n分散采购 货物类\n七、中标情况：设备采购"),
    ("货物类", "六、采购方式: | 分散采购 货物类 | 七、中标情况：设备采购"),
    ("货物类", "采购方式 | 分散采购货物类"),
    ("货物类", "采购方式\n分散采购货物类\n中标情况：设备采购"),
    ("货物类", "六、采购方式:分散采购货物类七、中标情况：设备采购"),
    ("网络设备", "预算说明：网络设备预算上限为6万元。中标标的：交换机"),
    ("软件开发服务", "采购需求：包括软件开发服务；中标标的：平台建设"),
    ("网络设备", "节能政策：网络设备应符合节能产品政府采购品目清单。"),
    ("网络设备", "网络设备应符合节能产品政府采购品目\n清单。"),
    ("网络设备", "网络设备应符合\n节能产品政府采购品目清单。"),
    ("货物类", "（2023-3货物类公开招标文件范本）\n项目名称：教室设备"),
    ("货物类", "政府采购履约验收书参考样本(货物类）\n采购单位：甲方"),
    ("货物类", "说明：该表为货物类项目履约验收的参考样表。"),
])
def test_category_occurring_only_in_non_award_context_is_rejected(monkeypatch, category, source):
    assert _extract(monkeypatch, source, category).entities[0].category == ""


@pytest.mark.parametrize("source", [
    "采购方式：分散采购 货物类\n品目：货物类",
    "采购方式：分散采购货物类；品目：货物类",
    "采购方式：分散采购货物类品目：货物类",
    "采购方式：分散采购货物类\n类型 | 名称 | 品牌\n货物类 | 设备甲 | 品牌甲",
    "采购方式：分散采购货物类\n货物类\n名称：设备甲；品牌：品牌甲",
])
def test_genuine_category_or_type_is_kept_even_if_also_in_procurement_method(monkeypatch, source):
    assert _extract(monkeypatch, source, "货物类").entities[0].category == "货物类"


def test_policy_mentions_do_not_erase_a_separate_real_category(monkeypatch):
    source = "网络设备应符合节能产品政府采购品目清单。\n品目名称 | 标的名称\n网络设备 | 交换机"
    assert _extract(monkeypatch, source, "网络设备").entities[0].category == "网络设备"


@pytest.mark.parametrize("category,source", [
    ("网络设备", "采购方式：公开招标 | 品目 | 网络设备"),
    ("网络设备", "采购方式：\n品目 | 网络设备"),
    ("货物类", "产品名称 | 类型 | 备注\n打印机 | 货物类 | 验收格式参照参考样表"),
    ("网络设备", "采购标的 | 说明 | 类型\n交换机 | 符合节能产品政府采购品目清单 | 网络设备"),
    ("C17010200 网络接入服务", "品目编号 | 品目名称 | 备注\nC17010200 | 网络接入服务 | 格式参照参考样表"),
])
def test_non_category_context_does_not_consume_real_field_or_table_cell(monkeypatch, category, source):
    assert _extract(monkeypatch, source, category).entities[0].category == category


@pytest.mark.parametrize("category,source", [
    ("货物类", "类型 | 货物类（验收格式参照参考样表）"),
    ("网络设备", "品目 | 网络设备（应符合节能产品政府采购品目清单）"),
    ("货物类", "名称 | 类型\n打印机 | 货物类（验收格式参照参考样表）"),
    ("网络设备", "类型 | 名称\n网络设备（应符合节能产品政府采购品目清单） | 交换机"),
    ("网络设备", "品目 | 网络设备（应符合节能产品政府采购品目清单（2019年版））"),
    ("货物类", "类型 | 货物类（验收格式参照参考样表（附件1））"),
])
def test_real_category_is_kept_when_its_cell_has_a_parenthetical_note(monkeypatch, category, source):
    assert _extract(monkeypatch, source, category).entities[0].category == category


@pytest.mark.parametrize("source", [
    "品目 | 其他货物（网络设备应符合节能产品政府采购品目清单）",
    "类型 | 名称\n其他货物（网络设备应符合节能产品政府采购品目清单） | 设备甲",
])
def test_category_word_only_inside_a_policy_note_is_not_a_field_value(monkeypatch, source):
    assert _extract(monkeypatch, source, "网络设备").entities[0].category == ""


@pytest.mark.parametrize("placeholder", ["未公示", "无"])
def test_unpublished_placeholders_are_empty_even_when_present_in_source(monkeypatch, placeholder):
    assert _extract(monkeypatch, "品目：" + placeholder, placeholder).entities[0].category == ""


def test_summary_rejects_invented_category_without_mutating_input():
    original = ExtractedEntity(product_name="虚构平台", category="软件开发服务", brand="虚构供应商")
    result = sanitize_summary_entities(
        [original], detail_status="no_attachment", source_text="名称 | 虚构平台",
    )
    assert len(result) == 1
    assert result[0].category == ""
    assert result[0].brand == "虚构供应商"
    assert original.category == "软件开发服务"


def test_category_cleanup_does_not_merge_previously_distinct_summary_rows():
    result = sanitize_summary_entities(
        [ExtractedEntity(product_name="虚构平台", category=c) for c in ("猜测类别甲", "猜测类别乙")],
        detail_status="no_attachment", source_text="名称：虚构平台",
    )
    assert len(result) == 2
    assert [row.category for row in result] == ["", ""]


def test_detail_entrypoint_rejects_invented_category(monkeypatch):
    extractor = object.__new__(LLMExtractor)
    monkeypatch.setattr(extractor, "_call_llm", lambda *args: json.dumps({
        "detail_entities": [{"product_name": "虚构设备", "category": "A02010103 服务器"}],
    }))
    entities, status, error = extractor.extract_details(
        texts={"明细.txt": "名称：虚构设备；数量：1台"}, tables={},
        announcement_id="fictional-category",
    )
    assert status == "available"
    assert error == ""
    assert len(entities) == 1
    assert entities[0].category == ""


def test_category_cleanup_does_not_merge_previously_distinct_detail_rows(monkeypatch):
    extractor = object.__new__(LLMExtractor)
    monkeypatch.setattr(extractor, "_call_llm", lambda *args: json.dumps({
        "detail_entities": [{"product_name": "虚构设备", "category": c} for c in ("猜测类别甲", "猜测类别乙")],
    }))
    entities, status, error = extractor.extract_details(
        texts={"明细.txt": "名称：虚构设备；数量：1台"}, tables={},
        announcement_id="fictional-two-category",
    )
    assert len(entities) == 2
    assert [row.category for row in entities] == ["", ""]
