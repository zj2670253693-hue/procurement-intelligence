"""Catch missing explicit column units, without price inference or pairing."""
from copy import deepcopy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
BASE = Path(__file__).resolve().parent.parent / "task1-standard-export-v13-20261013"
sys.path.insert(0, str(BASE))
import standard_export as legacy


def run(real_aid):
    import price_projection as p
    prior = BASE / "candidate-r9-name-final"
    tables = json.loads((prior / "tables.json").read_text(encoding="utf-8"))
    docs = json.loads((prior / "advanced-and-original-results.json").read_text(encoding="utf-8"))
    raw = [d["raw_result"] for d in docs if d["announcement_id"] == real_aid]
    tables["tables"]["标的表"] = [r for r in tables["tables"]["标的表"] if r["原公告ID"] == real_aid]
    joins = [j for j in json.loads((prior / "name-metadata-joins.json").read_text(encoding="utf-8")) if j["announcement_id"] == real_aid]
    before = deepcopy(tables)
    revised, changes = p.project_prices(tables, raw, joins, p.load_source_prices)
    assert tables == before
    return revised["tables"]["标的表"], changes


@pytest.mark.parametrize("aid,name,unit,total", [
    ("t20260206_26155601", "垂直滴水试验装置（IPX1、IPX2)", "5.00000000", "5.00000000"),
    ("t20260212_26176252", "警棍（伸缩警棍）", "0.02100000", "13.12500000"),
    ("t20260302_26210608", "多功能活细胞显微成像扫描仪（核心产品）", "83.600000", "83.6000"),
])
def test_unique_actual_column_unit_restores_price_without_multiplication(aid, name, unit, total):
    rows, changes = run(aid)
    row = next(r for r in rows if r["产品服务名称"] == name)
    assert (row["单价"], row["总价"]) == (unit, total)
    assert any(c["product_name"] == name for c in changes)


def test_restored_price_does_not_keep_a_false_unit_unknown_warning():
    rows, _ = run("t20260206_26155601")
    row = next(r for r in rows if r["产品服务名称"] == "垂直滴水试验装置（IPX1、IPX2)")
    assert "单价非明确金额或单位不明" not in row["备注"]
    assert "总价非明确金额或单位不明" not in row["备注"]
    assert "价格按本字段原文列单位换算" in row["证据来源"]


@pytest.mark.parametrize("raw,source,header,field,want", [
    ("50000.0000", "50,000.0000", "单价(元)", "unit_price", "5.00000000"),
    ("0.0000", "0.0000", "总价(元)", "total_price", "0.00000000"),
    ("50000", "60000", "单价(元)", "unit_price", ""),
    ("50000", "50000", "单价", "unit_price", ""),
    ("50000", "50000", "预算单价(元)", "unit_price", ""),
    ("50000", "50000；60000", "单价(元)", "unit_price", ""),
    ("50000；60000", "50000；60000", "单价(元)", "unit_price", ""),
    ("50%", "50%", "单价(元)", "unit_price", ""),
    ("50000元/人年", "50000元/人年", "单价(元)", "unit_price", ""),
    ("50000", "5万元", "单价(元)", "unit_price", ""),
    ("50000", "50000", "总价(元)", "unit_price", ""),
])
def test_price_cell_semantics_and_scalar_equivalence(raw, source, header, field, want):
    import price_projection as p
    assert p.explicit_column_price(raw, source, header, field) == want


def test_combined_source_prices_never_bind_by_position():
    rows, changes = run("t20260202_26139731")
    selected = [r for r in rows if r["产品服务名称"] in {"光跳纤架", "光熔纤柜"}]
    assert len(selected) == 2 and all(r["单价"] == "" for r in selected)
    assert not any(c["product_name"] in {"光跳纤架", "光熔纤柜"} for c in changes)
