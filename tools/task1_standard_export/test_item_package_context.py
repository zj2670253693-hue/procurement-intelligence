"""Only recover source-proven package context; never change extracted fields."""
import copy
import json
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import standard_export as m


def html_fixture(*, caption="合同包1(设备采购)", owner="甲公司", extra_caption="", duplicate=False):
    row = "<tr><td>设备甲</td><td>品牌甲</td><td>X1</td><td>1套</td><td>100</td><td>100</td></tr>"
    return ("<h2>项目中标公告</h2><div class='vF_detail_content'><table><tr><th>公告信息：</th></tr>"
            "<tr><td>采购项目名称</td><td>项目</td></tr><tr><td>采购单位</td><td>采购人</td></tr></table>"
            "<p>三、采购结果</p><p>合同包1</p><table><tr><th>供应商名称</th><th>中标金额(元)</th></tr>"
            "<tr><td>甲公司</td><td>100</td></tr></table>"
            f"<p>四、主要标的信息</p><p>{caption}：</p><p>货物类({owner})</p>"
            f"{('<p>' + extra_caption + '</p>') if extra_caption else ''}"
            "<table><tr><th>货物名称</th><th>品牌</th><th>规格型号</th><th>数量</th><th>单价(元)</th><th>总价(元)</th></tr>"
            + row + (row if duplicate else "") + "</table><p>五、评审专家名单</p></div>").encode()


def binding_key(source, name="设备甲"):
    bindings = [b for b in source["item_bindings"] if b["product_name"] == name]
    return bindings[0]["package_key"] if len(bindings) == 1 else ""


@pytest.mark.parametrize("aid,name,want", [
    ("t20260702_26858423", "便携式计算机", "包:1"),
    ("t20260702_26858423", "台式计算机", "包:2"),
    ("t20260206_26154814", "X射线计算机体层摄影设备", "包:2"),
    ("t20260303_26212365", "高能球磨机", "包:1"),
    ("t20260602_26674680", "电子政务外网公共服务域", "包:1"),
    ("t20260202_26139917", "团体重大疾病保险", "包:1"),
    ("t20260202_26139917", "团体意外伤害保险", "包:1"),
])
def test_real_unique_awarded_caption_is_not_discarded(aid, name, want):
    source = m.parse_source((m.SOURCE_ROOT / (aid + ".html")).read_bytes(), aid)
    assert binding_key(source, name) == want


def test_shared_uuid_recovers_without_changing_seven_fields_or_raw_data():
    source = m.parse_source(html_fixture(), "synthetic")
    raw = {"announcement_id": "synthetic", "entities": [{
        "product_name": "设备甲", "brand": "品牌甲", "spec_model": "X1", "quantity": "1套",
        "unit_price": "100元", "total_price": "100元", "category": "设备",
    }], "detail_entities": [{"product_name": "未核实附件记录"}]}
    before = copy.deepcopy(raw)
    bundle = m.project([raw], {"synthetic": source})
    item = bundle["tables"]["标的表"][0]
    assert item["UUID"] == bundle["tables"]["总表"][0]["UUID"] == "synthetic_01"
    assert item["标段/合同包"] == bundle["tables"]["总表"][0]["标段/合同包"]
    assert {label: item[label] for label in m.LABELS} == {
        "产品服务名称": "设备甲", "品目": "设备", "品牌": "品牌甲", "规格型号": "X1",
        "数量": "1套", "单价": "0.0100", "总价": "0.0100",
    }
    assert raw == before and bundle["advanced"][0]["raw_result"] == before
    assert len(bundle["tables"]["标的表"]) == 1


@pytest.mark.parametrize("kwargs", [
    {"caption": "合同包2(设备采购)"},  # Package2 has no accepted award record.
    {"caption": "合同包1、合同包2"},
    {"caption": "合同包1", "extra_caption": "合同包2、合同包3"},
    {"owner": "乙公司"},
    {"duplicate": True},
])
def test_ambiguous_unawarded_conflicting_or_repeated_rows_remain_unbound(kwargs):
    source = m.parse_source(html_fixture(**kwargs), "synthetic")
    assert binding_key(source) == ""


def test_actual_duplicate_attachment_only_rows_are_not_matched_by_order():
    aid = "t20260509_26538567"
    source = m.parse_source((m.SOURCE_ROOT / (aid + ".html")).read_bytes(), aid)
    snapshot = json.loads((m.HERE / "candidate-r4-final/advanced-and-original-results.json").read_text(encoding="utf-8"))
    raw = next(d["raw_result"] for d in snapshot if d["announcement_id"] == aid)
    before = copy.deepcopy(raw)
    rows = m.project([raw], {aid: source})["tables"]["标的表"]
    assert len(rows) == len(raw["entities"]) == 4
    assert [row["数量"] for row in rows[:2]] == ["16套", "4套"]
    assert all(row["UUID"] == aid and row["标段/合同包"] == "" for row in rows)
    assert raw == before


def test_no_accepted_award_for_second_service_package_is_not_inferred():
    aid = "t20260602_26674680"
    source = m.parse_source((m.SOURCE_ROOT / (aid + ".html")).read_bytes(), aid)
    assert binding_key(source, "电子政务外网行政服务域") == ""


@pytest.mark.parametrize("unknown", [
    "合同包A(设备采购)", "合同包待定(设备采购)", "包号：待定",
    "A包(设备采购)", "标包A", "第一标段", "不分包",
])
def test_unsupported_package_caption_clears_previous_numeric_package(unknown):
    source = m.parse_source(html_fixture(extra_caption=unknown), "synthetic")
    assert binding_key(source) == ""


@pytest.mark.parametrize("header,value", [
    ("采购包", "未知"), ("采购包", "/"), ("标段/合同包", "合同包2"), ("合同包号", "合同包2"),
])
def test_unknown_or_unsupported_explicit_package_column_cannot_borrow_caption(header, value):
    text = html_fixture().decode().replace("<th>货物名称</th>", f"<th>{header}</th><th>货物名称</th>")
    text = text.replace("<td>设备甲</td>", f"<td>{value}</td><td>设备甲</td>")
    source = m.parse_source(text.encode(), "synthetic")
    assert binding_key(source) == ""


def test_multiple_explicit_awards_do_not_infer_owner_or_change_unique_package_title():
    text = html_fixture().decode().replace(
        "<tr><td>甲公司</td><td>100</td></tr>",
        "<tr><td>甲公司</td><td>100</td></tr><tr><td>乙公司</td><td>200</td></tr>",
    )
    source = m.parse_source(text.encode(), "synthetic")
    assert len(source["awards"]) == 2
    assert binding_key(source) == "包:1"  # Explicit title, not inferred winner/amount.
