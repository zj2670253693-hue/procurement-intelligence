"""Outbound-policy contracts, not a simulation of model compliance.

Removing the base-layer granularity policy from a real API request must fail
these tests. The fixed transport response separately protects returned rows;
actual extraction quality is checked with the saved real-call pilot.
"""

import json

import pytest

from tests.test_quantity_prompt_contract import invoke, request_probe


@pytest.mark.parametrize("mode", ["summary", "legacy"])
@pytest.mark.parametrize("has_tables", [False, True])
def test_real_request_distinguishes_package_items_and_rate_tiers(request_probe, mode, has_tables):
    extractor, captured, _ = request_probe
    source = "主标的 | 采购包1 | 详见附件\n附件：设备甲\n高级职称人员900元/人/天；中级职称人员700元/人/天。"
    result = invoke(extractor, mode, source, has_tables)
    assert result.success
    assert len(captured) == 1
    messages = captured[0]["messages"]
    assert source in messages[1]["content"]
    system = messages[0]["content"]
    # Hand-written outbound obligations: package-only references must not
    # expand, real item rows must not collapse, and explicit rate tiers split.
    for obligation in ["基础层粒度规则", "采购包行", "不得展开附件设备清单",
                       "逐项列出的真实标的", "分档计价", "每个计价档位分别输出",
                       "不要把多个档位的费率合并", "附件缺失也不应返回空实体"]:
        assert obligation in system, f"Missing outbound granularity obligation: {obligation}"


@pytest.mark.parametrize("mode", ["summary", "legacy"])
@pytest.mark.parametrize("name,price,spec", [
    ("软件运维服务", "", "V3.0"),
    ("审计服务（高级职称人员）", "900元/人/天", "高级职称人员"),
    ("采购包1", "", ""),
])
def test_policy_does_not_rewrite_explicit_model_rows(request_probe, mode, name, price, spec):
    extractor, captured, entity = request_probe
    entity.update(product_name=name, unit_price=price, spec_model=spec)
    result = invoke(extractor, mode, f"产品服务名称 | 规格型号 | 单价\n{name} | {spec} | {price}")
    assert result.success
    assert len(captured) == 1
    assert len(result.entities) == 1
    assert result.entities[0].product_name == name
    assert result.entities[0].unit_price == price
    assert result.entities[0].spec_model == spec
    assert json.loads(result.raw_response)["entities"][0] == entity
