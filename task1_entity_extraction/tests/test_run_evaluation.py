import json
from pathlib import Path

from conftest import FIELDS, make_review_case, make_review_item, write_review_json
from run_evaluation import (
    evaluate_files,
    main,
    parse_args,
    write_error_csv,
    write_report,
)


def make_273_entity_cases():
    cases = []
    entity_number = 0
    for case_number in range(1, 41):
        count = 7 if case_number <= 33 else 6
        items = []
        for item_number in range(1, count + 1):
            entity_number += 1
            items.append(
                make_review_item(
                    id=entity_number,
                    data={
                        "product_name": f"虚构产品-{case_number}-{item_number}",
                        "category": "测试品目",
                        "brand": "虚构供应商",
                        "spec_model": f"X-{entity_number}",
                        "unit_price": f"{entity_number}元",
                        "quantity": "1台",
                        "total_price": f"{entity_number}元",
                    },
                )
            )
        cases.append(make_review_case(case_number, items))
    assert entity_number == 273
    return cases


def write_matching_predictions(tmp_path: Path, cases: list[dict]) -> Path:
    predictions = []
    for case in cases:
        predictions.append(
            {
                "announcement_id": case["announcement_id"],
                "entities": [
                    {field: str(item["data"].get(field, "") or "") for field in FIELDS}
                    for item in case["items"]
                    if not item.get("is_deleted")
                ],
                "success": True,
            }
        )
    path = tmp_path / "predictions.json"
    path.write_text(json.dumps(predictions, ensure_ascii=False), encoding="utf-8")
    return path


def test_parse_args_uses_human_gold_output_names_for_json():
    args = parse_args(["gold.json"])

    assert args.ground_truth == Path("gold.json")
    assert args.report_out.name == "evaluation_report_human_gold.json"
    assert args.errors_out.name == "evaluation_errors_human_gold.csv"
    assert args.expected_cases == 40


def test_evaluate_files_returns_40_cases_and_273_gold_entities(tmp_path):
    cases = make_273_entity_cases()
    ground_truth_path = write_review_json(tmp_path, cases)
    predictions_path = write_matching_predictions(tmp_path, cases)

    metrics, rows, gold_entity_count = evaluate_files(
        ground_truth_path,
        predictions_path,
    )

    assert metrics.total_samples == 40
    assert gold_entity_count == 273
    assert metrics.accuracy == 1.0
    assert rows == []


def test_write_report_records_only_source_basename_and_expected_metrics(tmp_path):
    cases = make_273_entity_cases()
    ground_truth_path = write_review_json(tmp_path, cases)
    predictions_path = write_matching_predictions(tmp_path, cases)
    metrics, _, count = evaluate_files(ground_truth_path, predictions_path)
    report_path = tmp_path / "report.json"

    write_report(
        report_path,
        metrics,
        source_name=str(tmp_path / "private" / "human-gold.json"),
        gold_entity_count=count,
    )

    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["source_file"] == "human-gold.json"
    assert str(tmp_path) not in report_path.read_text(encoding="utf-8")
    assert payload["gold_entity_count"] == 273
    assert payload["total_samples"] == 40
    assert payload["composite"] == 1.0
    assert "+" in payload["generated_at"] or payload["generated_at"].endswith("Z")


def test_write_error_csv_uses_utf8_bom_and_fixed_columns(tmp_path):
    path = tmp_path / "errors.csv"
    rows = [
        {
            "announcement_id": "fictional-001",
            "prediction_entity_index": 1,
            "gold_entity_index": 1,
            "product_name_context": "中文产品",
            "field": "brand",
            "prediction_value": "预测供应商",
            "gold_value": "金标供应商",
            "error_type": "wrong_value",
            "fp_contribution": 1,
            "fn_contribution": 1,
        }
    ]

    write_error_csv(path, rows)

    raw = path.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    assert "announcement_id,prediction_entity_index,gold_entity_index" in text
    assert "中文产品" in text


def test_main_validation_failure_does_not_overwrite_existing_outputs(tmp_path):
    cases = [make_review_case(case_number) for case_number in range(1, 41)]
    cases[0]["items"][0]["data"]["spec_model"] = "未公示"
    ground_truth_path = write_review_json(tmp_path, cases)
    predictions_path = write_matching_predictions(tmp_path, cases)
    report_path = tmp_path / "report.json"
    errors_path = tmp_path / "errors.csv"
    report_path.write_text("old-report", encoding="utf-8")
    errors_path.write_text("old-errors", encoding="utf-8")

    exit_code = main(
        [
            str(ground_truth_path),
            "--predictions",
            str(predictions_path),
            "--report-out",
            str(report_path),
            "--errors-out",
            str(errors_path),
        ]
    )

    assert exit_code == 1
    assert report_path.read_text(encoding="utf-8") == "old-report"
    assert errors_path.read_text(encoding="utf-8") == "old-errors"


def test_main_custom_outputs_leave_legacy_report_unchanged(tmp_path):
    cases = [make_review_case(case_number) for case_number in range(1, 41)]
    ground_truth_path = write_review_json(tmp_path, cases)
    predictions_path = write_matching_predictions(tmp_path, cases)
    report_path = tmp_path / "human-report.json"
    errors_path = tmp_path / "human-errors.csv"
    legacy_path = tmp_path / "evaluation_report.json"
    legacy_path.write_text("legacy-100-percent", encoding="utf-8")

    exit_code = main(
        [
            str(ground_truth_path),
            "--predictions",
            str(predictions_path),
            "--report-out",
            str(report_path),
            "--errors-out",
            str(errors_path),
        ]
    )

    assert exit_code == 0
    assert report_path.exists()
    assert errors_path.exists()
    assert legacy_path.read_text(encoding="utf-8") == "legacy-100-percent"
