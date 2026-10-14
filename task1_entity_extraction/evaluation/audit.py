"""Build a field-level error audit using the evaluator's exact matching rules."""

from __future__ import annotations

from typing import Any

from evaluation.metrics import Evaluator
from models.schemas import ExtractionResult


def _value(entity: dict, field: str) -> str:
    return str(entity.get(field, "") or "").strip()


def _row(
    *,
    announcement_id: str,
    prediction_entity_index: int | str,
    gold_entity_index: int | str,
    product_name_context: str,
    field: str,
    prediction_value: str,
    gold_value: str,
    error_type: str,
    fp_contribution: int,
    fn_contribution: int,
) -> dict[str, Any]:
    return {
        "announcement_id": announcement_id,
        "prediction_entity_index": prediction_entity_index,
        "gold_entity_index": gold_entity_index,
        "product_name_context": product_name_context,
        "field": field,
        "prediction_value": prediction_value,
        "gold_value": gold_value,
        "error_type": error_type,
        "fp_contribution": fp_contribution,
        "fn_contribution": fn_contribution,
    }


def build_error_rows(
    predictions: list[ExtractionResult],
    ground_truths: list[dict],
    evaluator: Evaluator | None = None,
) -> list[dict]:
    """Return deterministic, human-readable rows for every FP/FN contribution."""
    evaluator = evaluator or Evaluator()
    prediction_map: dict[str, ExtractionResult] = {}
    for prediction in predictions:
        if prediction.announcement_id in prediction_map:
            raise ValueError(f"预测结果中公告 ID 重复: {prediction.announcement_id}")
        prediction_map[prediction.announcement_id] = prediction

    rows: list[dict] = []
    for ground_truth in ground_truths:
        announcement_id = str(ground_truth.get("announcement_id", "")).strip()
        gold_entities = ground_truth.get("entities", [])
        prediction = prediction_map.get(announcement_id)
        pred_entities = (
            [entity.model_dump() for entity in prediction.entities]
            if prediction is not None
            else []
        )
        pairs, unmatched_pred, unmatched_gold = evaluator.align_entities(
            pred_entities, gold_entities
        )

        for pred_index, gold_index in pairs:
            pred_entity = pred_entities[pred_index]
            gold_entity = gold_entities[gold_index]
            context = _value(gold_entity, "product_name") or _value(
                pred_entity, "product_name"
            )
            for field in evaluator.FIELD_KEYS:
                prediction_value = _value(pred_entity, field)
                gold_value = _value(gold_entity, field)
                if not prediction_value and not gold_value:
                    continue
                if (
                    prediction_value
                    and gold_value
                    and evaluator.field_match(prediction_value, gold_value, field)
                ):
                    continue
                if prediction_value and gold_value:
                    error_type = "wrong_value"
                    fp_contribution, fn_contribution = 1, 1
                elif prediction_value:
                    error_type = "false_positive"
                    fp_contribution, fn_contribution = 1, 0
                else:
                    error_type = "false_negative"
                    fp_contribution, fn_contribution = 0, 1
                rows.append(
                    _row(
                        announcement_id=announcement_id,
                        prediction_entity_index=pred_index + 1,
                        gold_entity_index=gold_index + 1,
                        product_name_context=context,
                        field=field,
                        prediction_value=prediction_value,
                        gold_value=gold_value,
                        error_type=error_type,
                        fp_contribution=fp_contribution,
                        fn_contribution=fn_contribution,
                    )
                )

        for pred_index in unmatched_pred:
            pred_entity = pred_entities[pred_index]
            context = _value(pred_entity, "product_name")
            for field in evaluator.FIELD_KEYS:
                prediction_value = _value(pred_entity, field)
                if not prediction_value:
                    continue
                rows.append(
                    _row(
                        announcement_id=announcement_id,
                        prediction_entity_index=pred_index + 1,
                        gold_entity_index="",
                        product_name_context=context,
                        field=field,
                        prediction_value=prediction_value,
                        gold_value="",
                        error_type="extra_entity",
                        fp_contribution=1,
                        fn_contribution=0,
                    )
                )

        for gold_index in unmatched_gold:
            gold_entity = gold_entities[gold_index]
            context = _value(gold_entity, "product_name")
            for field in evaluator.FIELD_KEYS:
                gold_value = _value(gold_entity, field)
                if not gold_value:
                    continue
                rows.append(
                    _row(
                        announcement_id=announcement_id,
                        prediction_entity_index="",
                        gold_entity_index=gold_index + 1,
                        product_name_context=context,
                        field=field,
                        prediction_value="",
                        gold_value=gold_value,
                        error_type="missing_entity",
                        fp_contribution=0,
                        fn_contribution=1,
                    )
                )

    return rows
