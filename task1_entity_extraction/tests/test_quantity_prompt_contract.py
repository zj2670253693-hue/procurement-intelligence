"""Test the real outbound API request, not the model's extraction ability.

The transport double returns a fixed response; it never pretends to obey a
prompt. New contracts catch a missing quantity policy at the API boundary.
Quantity round-trip tests protect existing values from automatic deletion.
"""

import json

import httpx
from openai import OpenAI
import pytest

from extractor.llm_extractor import LLMExtractor


@pytest.fixture
def request_probe():
    captured = []
    response_entity = {
        "product_name": "维保服务", "category": "服务", "brand": "测试供应商",
        "spec_model": "", "unit_price": "", "quantity": "", "total_price": "",
    }

    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "fixture", "object": "chat.completion", "created": 0, "model": "fixture-model",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {
                "role": "assistant", "content": json.dumps({"entities": [response_entity]}, ensure_ascii=False)}}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        })

    extractor = LLMExtractor(provider="deepseek", api_key="test-placeholder", base_url="https://fixture.invalid/v1", model="fixture-model")
    extractor.client.close()
    extractor.client = OpenAI(api_key="test-placeholder", base_url="https://fixture.invalid/v1", max_retries=0,
                             http_client=httpx.Client(transport=httpx.MockTransport(respond)))
    try:
        yield extractor, captured, response_entity
    finally:
        extractor.client.close()


def invoke(extractor, mode, source, has_tables=True):
    if mode == "summary":
        return extractor.extract_summary(source, has_tables=has_tables, detail_status="missing")
    return extractor.extract(source, has_tables=has_tables, detail_status="missing")


@pytest.mark.parametrize("mode", ["summary", "legacy"])
@pytest.mark.parametrize("has_tables", [False, True])
def test_outbound_request_requires_quantity_evidence_and_forbids_default_one(request_probe, mode, has_tables):
    extractor, captured, _ = request_probe
    source = "名称 | 序号 | 服务时间 | 服务范围\n维保服务 | 1 | 3年 | 1338人\n不符合20.1项规定，详见附件。"
    result = invoke(extractor, mode, source, has_tables)
    assert result.success
    assert len(captured) == 1
    messages = captured[0]["messages"]
    assert [m["role"] for m in messages] == ["system", "user"]
    assert source in messages[1]["content"]
    system = messages[0]["content"]
    # These are required instructions in the actual model request, not a
    # simulated assertion that the model extracted an empty quantity.
    for requirement in ["数量证据", "禁止默认填", "1项", "序号", "条款编号", "服务期限",
                        "服务范围", "同名标的", "不能唯一对应", "明确数量为1项"]:
        assert requirement in system, f"Missing quantity request policy: {requirement}"


@pytest.mark.parametrize("mode", ["summary", "legacy"])
@pytest.mark.parametrize("quantity", ["", "0台", "1项", "1.0000项", "3年", "1338人"])
def test_quantity_response_is_not_rewritten_by_the_prompt_change(request_probe, mode, quantity):
    extractor, captured, entity = request_probe
    entity["quantity"] = quantity
    source = f"名称 | 数量\n维保服务 | {quantity}"
    result = invoke(extractor, mode, source)
    assert result.success
    assert len(captured) == 1
    assert len(result.entities) == 1
    assert result.entities[0].quantity == quantity
    assert json.loads(result.raw_response)["entities"][0]["quantity"] == quantity
