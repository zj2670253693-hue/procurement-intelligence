from collections import defaultdict

from evaluation.audit import build_error_rows
from evaluation.metrics import Evaluator
from models.schemas import ExtractedEntity, ExtractionResult


FIELDS = Evaluator.FIELD_KEYS


def entity(**values):
    data = {field: "" for field in FIELDS}
    data.update(values)
    return data


def prediction(announcement_id, entities):
    return ExtractionResult(
        announcement_id=announcement_id,
        entities=[ExtractedEntity(**item) for item in entities],
    )


def test_build_error_rows_classifies_all_five_error_types():
    ground_truths = [
        {
            "announcement_id": "fictional-001",
            "entities": [
                entity(
                    product_name="核心产品",
                    category="设备",
                    brand="金标品牌",
                    quantity="2台",
                    total_price="200元",
                ),
                entity(product_name="遗漏服务", brand="遗漏供应商"),
            ],
        }
    ]
    predictions = [
        prediction(
            "fictional-001",
            [
                entity(
                    product_name="核心产品",
                    category="",
                    brand="预测品牌",
                    spec_model="X-1",
                    quantity="2台",
                    total_price="200元",
                ),
                entity(product_name="多余产品", unit_price="50元"),
            ],
        )
    ]

    rows = build_error_rows(predictions, ground_truths)

    assert {(row["field"], row["error_type"]) for row in rows} == {
        ("category", "false_negative"),
        ("brand", "wrong_value"),
        ("spec_model", "false_positive"),
        ("product_name", "extra_entity"),
        ("unit_price", "extra_entity"),
        ("product_name", "missing_entity"),
        ("brand", "missing_entity"),
    }
    wrong = next(row for row in rows if row["error_type"] == "wrong_value")
    assert wrong["prediction_value"] == "预测品牌"
    assert wrong["gold_value"] == "金标品牌"
    assert wrong["fp_contribution"] == 1
    assert wrong["fn_contribution"] == 1


def test_build_error_rows_uses_shared_alignment_when_entity_order_changes():
    gold_entities = [
        entity(product_name="产品甲", brand="供应商甲", total_price="10元"),
        entity(product_name="产品乙", brand="供应商乙", total_price="20元"),
    ]
    ground_truths = [
        {"announcement_id": "fictional-002", "entities": gold_entities}
    ]
    predictions = [prediction("fictional-002", list(reversed(gold_entities)))]

    rows = build_error_rows(predictions, ground_truths)

    assert rows == []


def test_audit_fp_fn_contributions_reconcile_with_metrics_by_field():
    ground_truths = [
        {
            "announcement_id": "fictional-003",
            "entities": [
                entity(
                    product_name="产品甲",
                    category="设备",
                    brand="金标供应商",
                    unit_price="100元",
                    quantity="2台",
                    total_price="200元",
                ),
                entity(product_name="遗漏产品", category="服务"),
            ],
        }
    ]
    predictions = [
        prediction(
            "fictional-003",
            [
                entity(
                    product_name="产品甲",
                    category="设备",
                    brand="错误供应商",
                    spec_model="S-1",
                    unit_price="",
                    quantity="2台",
                    total_price="200元",
                ),
                entity(product_name="多余产品", total_price="999元"),
            ],
        )
    ]

    evaluator = Evaluator()
    metrics = evaluator.evaluate(predictions, ground_truths)
    rows = build_error_rows(predictions, ground_truths, evaluator=evaluator)
    contributions = defaultdict(lambda: {"fp": 0, "fn": 0})
    for row in rows:
        contributions[row["field"]]["fp"] += row["fp_contribution"]
        contributions[row["field"]]["fn"] += row["fn_contribution"]

    for field, stats in metrics.field_stats.items():
        expected_fp = int(stats["predicted"] - stats["correct"])
        expected_fn = int(stats["relevant"] - stats["correct"])
        assert contributions[field] == {"fp": expected_fp, "fn": expected_fn}


def test_public_matcher_and_private_compatibility_wrappers_agree():
    evaluator = Evaluator()
    predictions = [entity(product_name="服务器A", total_price="1万元")]
    gold = [entity(product_name="服务器A", total_price="10000元")]

    assert evaluator.align_entities(predictions, gold) == evaluator._align(predictions, gold)
    assert evaluator.field_match("1万元", "10000元", "total_price")
    assert evaluator.field_match("1万元", "10000元", "total_price") == evaluator._field_match(
        "1万元", "10000元", "total_price"
    )


def test_numeric_wrong_values_have_consistent_audit_contributions():
    ground_truths = [{
        "announcement_id": "fictional-numeric-audit",
        "entities": [entity(product_name="数值测试产品", quantity="10台", total_price="100元")],
    }]
    predictions = [prediction("fictional-numeric-audit", [
        entity(product_name="数值测试产品", quantity="1.0台", total_price="100元/年"),
    ])]
    evaluator = Evaluator()
    metrics = evaluator.evaluate(predictions, ground_truths)
    rows = build_error_rows(predictions, ground_truths, evaluator=evaluator)
    assert {(row["field"], row["error_type"], row["fp_contribution"], row["fn_contribution"])
            for row in rows} == {
        ("quantity", "wrong_value", 1, 1), ("total_price", "wrong_value", 1, 1),
    }
    assert metrics.correct_fields == 1
    assert metrics.total_fields == 5
    for field in ("quantity", "total_price"):
        assert metrics.field_stats[field]["correct"] == 0
        assert metrics.field_stats[field]["predicted"] == 1
        assert metrics.field_stats[field]["relevant"] == 1
