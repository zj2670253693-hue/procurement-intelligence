import json
from pathlib import Path

import pandas as pd
import pytest

from conftest import FIELDS, make_review_case, make_review_cases, make_review_item, write_review_json
from evaluation.ground_truth import (
    GroundTruthFormatError,
    load_ground_truth,
    load_review_excel,
    load_review_json,
)


def test_load_review_json_builds_40_case_ground_truth(tmp_path):
    path = write_review_json(tmp_path, make_review_cases())

    result = load_review_json(path)

    assert len(result) == 40
    assert result[0] == {
        "announcement_id": "fictional-001",
        "entities": [
            {
                "product_name": "测试产品",
                "category": "测试品目",
                "brand": "测试供应商",
                "spec_model": "X-1",
                "unit_price": "100元",
                "quantity": "1台",
                "total_price": "100元",
            }
        ],
    }


def test_load_review_json_omits_deleted_and_empty_items_but_keeps_added(tmp_path):
    cases = make_review_cases()
    cases[0]["items"] = [
        make_review_item(id=1, is_deleted=True),
        make_review_item(id=2, initial_verdict="删除该行"),
        make_review_item(id=3, data={field: "" for field in FIELDS}),
        make_review_item(
            id=4,
            is_added=True,
            data={"product_name": "补录服务", "brand": "虚构供应商"},
        ),
    ]
    path = write_review_json(tmp_path, cases)

    result = load_review_json(path)

    assert result[0]["entities"] == [
        {
            "product_name": "补录服务",
            "category": "",
            "brand": "虚构供应商",
            "spec_model": "",
            "unit_price": "",
            "quantity": "",
            "total_price": "",
        }
    ]


def test_load_review_json_uses_empty_string_for_missing_or_null_fields(tmp_path):
    cases = make_review_cases()
    cases[0]["items"] = [
        make_review_item(data={"product_name": "仅名称", "brand": None})
    ]
    path = write_review_json(tmp_path, cases)

    entity = load_review_json(path)[0]["entities"][0]

    assert entity == {
        "product_name": "仅名称",
        "category": "",
        "brand": "",
        "spec_model": "",
        "unit_price": "",
        "quantity": "",
        "total_price": "",
    }


def test_load_review_json_rejects_unpublished_placeholder(tmp_path):
    cases = make_review_cases()
    cases[8]["items"][0]["data"]["spec_model"] = "未公示"
    path = write_review_json(tmp_path, cases)

    with pytest.raises(GroundTruthFormatError, match="fictional-009.*spec_model.*未公示"):
        load_review_json(path)


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda cases: cases[1].update(announcement_id=cases[0]["announcement_id"]), "重复"),
        (lambda cases: cases[0].update(announcement_id="  "), "announcement_id"),
        (lambda cases: cases[0].update(items={}), "items"),
        (lambda cases: cases[0]["items"][0].update(data=[]), "data"),
    ],
)
def test_load_review_json_rejects_invalid_case_or_item_structure(tmp_path, mutate, message):
    cases = make_review_cases()
    mutate(cases)
    path = write_review_json(tmp_path, cases)

    with pytest.raises(GroundTruthFormatError, match=message):
        load_review_json(path)


@pytest.mark.parametrize("payload", [[], {}, {"cases": {}}, {"cases": ["bad"]}])
def test_load_review_json_rejects_invalid_top_level_structure(tmp_path, payload):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    with pytest.raises(GroundTruthFormatError):
        load_review_json(path, expected_cases=1)


def test_load_review_json_rejects_wrong_case_count(tmp_path):
    path = write_review_json(tmp_path, make_review_cases(39))

    with pytest.raises(GroundTruthFormatError, match="40.*39"):
        load_review_json(path)


def test_load_ground_truth_rejects_unsupported_suffix(tmp_path):
    path = tmp_path / "gold.csv"
    path.write_text("x", encoding="utf-8")

    with pytest.raises(GroundTruthFormatError, match=".csv"):
        load_ground_truth(path)


def test_load_review_excel_preserves_existing_reviewed_format(tmp_path):
    path = tmp_path / "ground_truth.xlsx"
    row = {
        "公告ID": "fictional-excel-001",
        "序号": 1,
        "产品服务名称": "测试产品",
        "品目": "测试品目",
        "品牌（产品供应商）": "测试供应商",
        "规格型号": "X-1",
        "单价": "100元",
        "数量": "1台",
        "总价": "100元",
        "核验结论": "正确",
    }
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        pd.DataFrame([row]).to_excel(writer, sheet_name="标注(待核验)", index=False)

    result = load_review_excel(path)

    assert result == [
        {
            "announcement_id": "fictional-excel-001",
            "entities": [
                {
                    "product_name": "测试产品",
                    "category": "测试品目",
                    "brand": "测试供应商",
                    "spec_model": "X-1",
                    "unit_price": "100元",
                    "quantity": "1台",
                    "total_price": "100元",
                }
            ],
        }
    ]


def test_load_ground_truth_dispatches_json_with_explicit_case_count(tmp_path):
    path = write_review_json(tmp_path, [make_review_case(1)])

    result = load_ground_truth(path, expected_cases=1)

    assert [item["announcement_id"] for item in result] == ["fictional-001"]
