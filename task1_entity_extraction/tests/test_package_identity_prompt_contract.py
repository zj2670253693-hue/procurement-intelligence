"""Outbound package identity policy, not a claim that a model obeys it."""
import json

import pytest

from extractor.llm_extractor import LLMExtractor
from models.schemas import ExtractedEntity, deduplicate_entities
from tests.test_quantity_prompt_contract import invoke, request_probe


@pytest.mark.parametrize("mode", ["summary", "legacy"])
@pytest.mark.parametrize("has_tables", [False, True])
def test_real_request_preserves_needed_package_number_in_product_name(request_probe, mode, has_tables):
    extractor, captured, _ = request_probe
    result = invoke(extractor, mode, "第1包与第2包均由甲公司成交，同名咨询服务，资深人员800元/人/天。", has_tables)
    assert result.success
    assert len(captured) == 1
    system = captured[0]["messages"][0]["content"]
    for obligation in ["产品服务名称中保留原文明示的包号", "服务名称+包号+档位",
                       "不能按结果行顺序猜包", "咨询服务（第1包，资深人员）",
                       "咨询服务（第2包，资深人员）"]:
        assert obligation in system, f"Missing outbound package identity obligation: {obligation}"


def test_dedup_keeps_explicit_cross_package_identity_and_removes_same_package_repeat():
    one = ExtractedEntity(product_name="咨询服务（第1包，资深人员）", brand="甲公司", spec_model="资深人员", unit_price="800元/人/天")
    two = one.model_copy(update={"product_name": "咨询服务（第2包，资深人员）"})
    result = deduplicate_entities([two, one, one.model_copy(deep=True)])
    assert [row.product_name for row in result] == ["咨询服务（第2包，资深人员）", "咨询服务（第1包，资深人员）"]
    assert result[0] is two and result[1] is one


@pytest.mark.parametrize("mode", ["summary", "legacy"])
def test_returned_explicit_package_names_survive_full_entrypoint(mode, monkeypatch):
    extractor = object.__new__(LLMExtractor)
    rows = [
        ExtractedEntity(product_name="咨询服务（第2包，资深人员）", brand="甲公司", spec_model="资深人员", unit_price="800元/人/天"),
        ExtractedEntity(product_name="咨询服务（第1包，资深人员）", brand="甲公司", spec_model="资深人员", unit_price="800元/人/天"),
    ]
    response = json.dumps({"entities": [row.model_dump() for row in rows]}, ensure_ascii=False)
    monkeypatch.setattr(extractor, "_call_llm", lambda *args: response)
    source = "服务名称 | 采购包 | 档位 | 单价\n咨询服务 | 第1包 | 资深人员 | 800元/人/天\n咨询服务 | 第2包 | 资深人员 | 800元/人/天"
    result = invoke(extractor, mode, source)
    assert result.success
    assert result.raw_response == response
    assert [row.product_name for row in result.entities] == ["咨询服务（第2包，资深人员）", "咨询服务（第1包，资深人员）"]
    assert all(set(row.model_dump()) == {"product_name", "category", "brand", "spec_model", "unit_price", "quantity", "total_price"} for row in result.entities)
