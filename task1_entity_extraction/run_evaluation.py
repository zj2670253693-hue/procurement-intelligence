"""Evaluate extraction results against reviewed Excel or human-review JSON."""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from evaluation.audit import build_error_rows
from evaluation.ground_truth import (
    GroundTruthFormatError,
    load_ground_truth as _load_ground_truth,
)
from evaluation.metrics import Evaluator
from models.schemas import EvaluationMetrics, ExtractedEntity, ExtractionResult


ERROR_COLUMNS = (
    "announcement_id",
    "prediction_entity_index",
    "gold_entity_index",
    "product_name_context",
    "field",
    "prediction_value",
    "gold_value",
    "error_type",
    "fp_contribution",
    "fn_contribution",
)


def load_ground_truth(xlsx_path: Path, require_reviewed: bool = True) -> list:
    """Backward-compatible loader used by earlier callers and tests."""
    return _load_ground_truth(
        Path(xlsx_path),
        require_reviewed=require_reviewed,
    )


def load_predictions(raw_path: Path) -> list[ExtractionResult]:
    """Load raw extraction results."""
    raw_path = Path(raw_path)
    try:
        data = json.loads(raw_path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise ValueError(f"找不到提取结果: {raw_path}") from exc
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"提取结果不是合法 UTF-8 JSON: {raw_path.name}") from exc
    if not isinstance(data, list):
        raise ValueError("提取结果 JSON 顶层必须是数组")
    return [
        ExtractionResult(
            announcement_id=item["announcement_id"],
            entities=[ExtractedEntity(**entity) for entity in item.get("entities", [])],
            success=item.get("success", True),
        )
        for item in data
    ]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="任务一离线人工金标评测")
    parser.add_argument(
        "ground_truth",
        nargs="?",
        type=Path,
        default=config.OUTPUT_DIR / "validation_set" / "ground_truth.xlsx",
        help="人工金标 .json 或已核验 .xlsx 文件",
    )
    parser.add_argument(
        "--predictions",
        type=Path,
        default=config.OUTPUT_DIR / "raw_results.json",
        help="模型原始提取结果 JSON",
    )
    parser.add_argument("--expected-cases", type=int, default=40)
    parser.add_argument("--allow-unreviewed", action="store_true")
    parser.add_argument("--report-out", type=Path)
    parser.add_argument("--errors-out", type=Path)
    args = parser.parse_args(argv)

    is_human_json = args.ground_truth.suffix.lower() == ".json"
    if args.report_out is None:
        args.report_out = config.OUTPUT_DIR / (
            "evaluation_report_human_gold.json"
            if is_human_json
            else "evaluation_report.json"
        )
    if args.errors_out is None:
        args.errors_out = config.OUTPUT_DIR / (
            "evaluation_errors_human_gold.csv"
            if is_human_json
            else "evaluation_errors.csv"
        )
    return args


def evaluate_files(
    ground_truth_path: Path,
    predictions_path: Path,
    *,
    expected_cases: int = 40,
    require_reviewed: bool = True,
) -> tuple[EvaluationMetrics, list[dict], int]:
    """Load both inputs and return metrics, audit rows, and gold entity count."""
    ground_truths = _load_ground_truth(
        Path(ground_truth_path),
        expected_cases=expected_cases,
        require_reviewed=require_reviewed,
    )
    predictions = load_predictions(Path(predictions_path))
    evaluator = Evaluator()
    metrics = evaluator.evaluate(predictions, ground_truths)
    error_rows = build_error_rows(predictions, ground_truths, evaluator=evaluator)
    gold_entity_count = sum(len(item.get("entities", [])) for item in ground_truths)
    return metrics, error_rows, gold_entity_count


def _report_payload(
    metrics: EvaluationMetrics,
    *,
    source_name: str,
    gold_entity_count: int,
) -> dict:
    return {
        "source_file": Path(source_name).name,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "gold_entity_count": gold_entity_count,
        "accuracy": metrics.accuracy,
        "precision": metrics.precision,
        "recall": metrics.recall,
        "f1_score": metrics.f1_score,
        "composite": round(
            metrics.accuracy * 0.4 + metrics.precision * 0.3 + metrics.recall * 0.3,
            4,
        ),
        "total_samples": metrics.total_samples,
        "total_fields": metrics.total_fields,
        "correct_fields": metrics.correct_fields,
        "field_stats": metrics.field_stats,
    }


def write_report(
    path: Path,
    metrics: EvaluationMetrics,
    *,
    source_name: str,
    gold_entity_count: int,
) -> None:
    """Atomically write a UTF-8 metric report."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        temporary.write_text(
            json.dumps(
                _report_payload(
                    metrics,
                    source_name=source_name,
                    gold_entity_count=gold_entity_count,
                ),
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def write_error_csv(path: Path, rows: list[dict]) -> None:
    """Atomically write Excel-friendly UTF-8 BOM audit rows."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        with temporary.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=ERROR_COLUMNS, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _write_outputs(
    report_path: Path,
    errors_path: Path,
    metrics: EvaluationMetrics,
    error_rows: list[dict],
    *,
    source_name: str,
    gold_entity_count: int,
) -> None:
    """Stage both serializations before replacing either official output."""
    report_path = Path(report_path)
    errors_path = Path(errors_path)
    report_stage = report_path.with_name(f".{report_path.name}.staged")
    errors_stage = errors_path.with_name(f".{errors_path.name}.staged")
    try:
        write_report(
            report_stage,
            metrics,
            source_name=source_name,
            gold_entity_count=gold_entity_count,
        )
        write_error_csv(errors_stage, error_rows)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        errors_path.parent.mkdir(parents=True, exist_ok=True)
        report_stage.replace(report_path)
        errors_stage.replace(errors_path)
    finally:
        report_stage.unlink(missing_ok=True)
        errors_stage.unlink(missing_ok=True)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        metrics, error_rows, gold_entity_count = evaluate_files(
            args.ground_truth,
            args.predictions,
            expected_cases=args.expected_cases,
            require_reviewed=not args.allow_unreviewed,
        )
        _write_outputs(
            args.report_out,
            args.errors_out,
            metrics,
            error_rows,
            source_name=args.ground_truth.name,
            gold_entity_count=gold_entity_count,
        )
    except (GroundTruthFormatError, ValueError, OSError) as exc:
        print(f"[错误] {exc}")
        return 1

    print(
        f"[评测] 人工金标公告 {metrics.total_samples} 篇，"
        f"标准标的物 {gold_entity_count} 条"
    )
    Evaluator().print_report(metrics)
    print(f"\n[输出] 评测报告: {args.report_out}")
    print(f"[输出] 错误明细: {args.errors_out}（{len(error_rows)} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
