"""Cosmetic identity matching must never become a new money/source rewrite."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import standard_export as m


def fixture():
    raw = {"announcement_id": "synthetic", "entities": [{
        "product_name": "设备 甲（型号）", "category": "设备", "brand": "品牌甲", "spec_model": "X1",
        "quantity": "1套", "unit_price": "100", "total_price": "100",
    }], "detail_entities": [{"product_name": "附件未核实项"}]}
    source = {"project_name": "项目", "procurement_unit": "单位", "divided": True,
              "packages": [{"key": "包:1", "label": "合同包1"}], "classification": "award_candidate",
              "source_sha256": "test", "warnings": [], "bidders": [],
              "awards": [{"package_key": "包:1", "supplier": "甲公司", "amount": "100元"}],
              "item_bindings": [{"product_name": "设备甲(型号)", "package_key": "包:1",
                                 "unit_price_unit": "元", "total_price_unit": "元", "evidence": "原文完整标的行",
                                 "source_identity": {"proved": True, "source_table": 1, "source_row": 1,
                                                     "complete_name": "设备甲(型号)",
                                                     "brand": "品牌甲", "spec_model": "X1"}}]}
    return raw, source


def item(raw, source):
    return m.project([raw], {raw["announcement_id"]: source})["tables"]["标的表"][0]


def test_cosmetic_name_links_metadata_but_never_uses_new_money_units():
    raw, source = fixture()
    before = copy.deepcopy(raw)
    row = item(raw, source)
    assert row["UUID"] == "synthetic_01" and row["标段/合同包"] == "合同包1"
    assert {label: row[label] for label in m.LABELS} == {
        "产品服务名称": "设备 甲（型号）", "品目": "设备", "品牌": "品牌甲", "规格型号": "X1",
        "数量": "1套", "单价": "", "总价": "",
    }
    assert raw == before


@pytest.mark.parametrize("aid,name,uuid", [
    ("t20260206_26154814", "电子面弓（下颌运动分析系统）", "t20260206_26154814_03"),
    ("t20260206_26155601", "垂直滴水试验装置（IPX1、IPX2)", "t20260206_26155601_01"),
    ("t20260609_26715701", "1标段 全自动样品处理系统", "t20260609_26715701_01"),
])
def test_real_cosmetic_name_relinks_without_altering_any_seven_fields(aid, name, uuid):
    prior = m.HERE / "candidate-r6-context-final"
    docs = json.loads((prior / "advanced-and-original-results.json").read_text(encoding="utf-8"))
    raw = next(d["raw_result"] for d in docs if d["announcement_id"] == aid)
    old_rows = json.loads((prior / "tables.json").read_text(encoding="utf-8"))["tables"]["标的表"]
    old = next(r for r in old_rows if r["原公告ID"] == aid and r["产品服务名称"] == name)
    source = m.parse_source((m.SOURCE_ROOT / (aid + ".html")).read_bytes(), aid)
    rows = m.project([raw], {aid: source})["tables"]["标的表"]
    row = next(r for r in rows if r["产品服务名称"] == name)
    assert row["UUID"] == uuid
    assert all(row[h] == old[h] for h in m.LABELS)
    join = m.project([raw], {aid: source})["name_metadata_joins"]
    proof = next(r["source_identity"] for r in join if r["raw_product_name"] == name)
    from source_name_join import cosmetic_name_key
    assert cosmetic_name_key(proof["complete_name"]) == cosmetic_name_key(name)


def test_unknown_package_can_gain_source_metadata_without_gaining_package_or_money():
    raw, source = fixture()
    source["item_bindings"][0]["package_key"] = ""
    row = item(raw, source)
    assert row["UUID"] == "synthetic" and row["标段/合同包"] == ""
    assert "原文完整标的行" in row["证据来源"]
    assert row["单价"] == row["总价"] == ""


@pytest.mark.parametrize("raw_name,source_name", [
    ("设备-甲", "设备甲"), ("设备－甲", "设备-甲"), ("deviceA", "devicea"),
    ("设备Ⅰ型", "设备I型"), ("设备①", "设备1"), ("设备甲详见附件", "设备甲"),
    ("1标段设备甲", "设备甲"), ("设备甲", "设备甲乙"),
])
def test_other_characters_prefixes_references_and_fuzzy_names_are_not_normalized(raw_name, source_name):
    raw, source = fixture()
    raw["entities"][0]["product_name"] = raw_name
    source["item_bindings"][0]["product_name"] = source_name
    source["item_bindings"][0]["source_identity"]["complete_name"] = source_name
    assert item(raw, source)["UUID"] == "synthetic"


def test_canonical_source_collision_blocks_fallback_including_weak_binding():
    raw, source = fixture()
    extra = copy.deepcopy(source["item_bindings"][0])
    extra["product_name"] = "设备 甲(型号)"
    extra.pop("source_identity")
    source["item_bindings"].append(extra)
    assert item(raw, source)["UUID"] == "synthetic"


def test_canonical_raw_collision_blocks_only_new_fallback_and_preserves_old_exact_match():
    raw, source = fixture()
    extra = copy.deepcopy(raw["entities"][0])
    extra["product_name"] = "设备甲(型号)"
    raw["entities"].append(extra)
    rows = m.project([raw], {"synthetic": source})["tables"]["标的表"]
    assert rows[0]["UUID"] == "synthetic" and rows[0]["单价"] == ""
    assert rows[1]["UUID"] == "synthetic_01" and rows[1]["单价"] == "0.0100"


@pytest.mark.parametrize("field,value", [("brand", "另一个品牌"), ("spec_model", "X2"), ("spec_model", "x1")])
def test_disclosed_brand_or_spec_conflict_blocks_cosmetic_fallback(field, value):
    raw, source = fixture()
    raw["entities"][0][field] = value
    assert item(raw, source)["UUID"] == "synthetic"


@pytest.mark.parametrize("identity", [None, {}, {"proved": False}, {"proved": "true"}])
def test_unproved_or_weak_combined_name_evidence_cannot_gain_fallback(identity):
    raw, source = fixture()
    source["item_bindings"][0]["source_identity"] = identity
    assert item(raw, source)["UUID"] == "synthetic"


def test_unaccepted_package_cannot_be_filled_by_name_matching():
    raw, source = fixture()
    source["awards"] = []
    assert item(raw, source)["UUID"] == "synthetic"


def test_proof_without_original_complete_name_cannot_enable_new_fallback():
    raw, source = fixture()
    source["item_bindings"][0]["source_identity"].pop("complete_name")
    assert item(raw, source)["UUID"] == "synthetic"


@pytest.mark.parametrize("source_name,raw_name", [
    ("设备①", "设备 1"), ("设备Ａ", "设备 A"), ("设备Ⅰ", "设备 I"),
    ("设备1详见附件", "设备 1"),
])
def test_real_parser_does_not_launder_unapproved_name_changes_into_new_fallback(source_name, raw_name):
    aid = "t20260206_26154814"
    html = (m.SOURCE_ROOT / (aid + ".html")).read_bytes().decode("utf-8-sig")
    original = "电子面弓（下颌运动分析系统）"
    assert original in html
    source = m.parse_source(html.replace(original, source_name).encode("utf-8"), "synthetic")
    raw, _ = fixture()
    raw["entities"][0].update(product_name=raw_name, brand="zebris", spec_model="zebris JMAOptic")
    row = item(raw, source)
    assert row["UUID"] == "synthetic" and row["标段/合同包"] == ""
    assert row["证据来源"] == "V12基础结果；未逐项核实"
    assert row["单价"] == row["总价"] == ""
