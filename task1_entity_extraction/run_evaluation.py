"""
跑评测：用验证集计算当前提取方案的 准确率 / 精确率 / 召回率

赛题综合得分 = 准确率×0.4 + 精确率×0.3 + 召回率×0.3

用法：
  python run_evaluation.py                     # 默认用 output/validation_set/ground_truth.xlsx
  python run_evaluation.py path/to/gt.xlsx
  python run_evaluation.py --source-qwen       # 用 qwen 的结果做对比路线（需先产出结果文件）
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

import config
from evaluation.metrics import Evaluator
from models.schemas import ExtractedEntity, ExtractionResult

FIELDS = ["product_name", "category", "brand", "spec_model",
          "unit_price", "quantity", "total_price"]
FIELD_LABELS = [config.FIELD_LABELS[f] for f in FIELDS]


REVIEWED_VERDICTS = {
    "正确", "错误", "修改", "已修改", "新增",
    "删除", "删除该行", "delete", "该公告无标的物", "无标的物",
}


def load_ground_truth(xlsx_path: Path, require_reviewed: bool = True) -> list:
    """从人工核验过的 Excel 读取标准答案"""
    df = pd.read_excel(xlsx_path, sheet_name="标注(待核验)").fillna("")

    if require_reviewed:
        verdicts = df["核验结论"].astype(str).str.strip()
        unreviewed = df.index[~verdicts.isin(REVIEWED_VERDICTS)].tolist()
        if unreviewed:
            preview = "、".join(str(i + 2) for i in unreviewed[:10])
            raise ValueError(
                f"验证集还有 {len(unreviewed)} 行未完成人工核验"
                f"（Excel 行号示例：{preview}）。请先填写“核验结论”，"
                "或仅在调试时使用 --allow-unreviewed。"
            )

    gt = {}
    dropped = 0
    for _, row in df.iterrows():
        aid = str(row.get("公告ID", "")).strip()
        if not aid:
            continue

        verdict = str(row.get("核验结论", "")).strip()
        if verdict in ("删除", "删除该行", "delete"):
            dropped += 1
            continue

        entity = {f: str(row.get(label, "")).strip()
                  for f, label in zip(FIELDS, FIELD_LABELS)}
        if not any(entity.values()):
            continue

        gt.setdefault(aid, {"announcement_id": aid, "entities": []})
        gt[aid]["entities"].append(entity)

    if dropped:
        print(f"[验证集] 按核验结论删除了 {dropped} 行")
    return list(gt.values())


def load_predictions(raw_path: Path) -> list:
    """从提取结果读取预测值"""
    with open(raw_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [
        ExtractionResult(
            announcement_id=item["announcement_id"],
            entities=[ExtractedEntity(**e) for e in item.get("entities", [])],
            success=item.get("success", True),
        )
        for item in data
    ]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    allow_unreviewed = "--allow-unreviewed" in sys.argv[1:]
    xlsx_path = Path(args[0]) if args else config.OUTPUT_DIR / "validation_set" / "ground_truth.xlsx"

    if not xlsx_path.exists():
        print(f"[错误] 找不到验证集文件: {xlsx_path}")
        print("       请先运行: python build_validation_set.py")
        sys.exit(1)

    raw_path = config.OUTPUT_DIR / "raw_results.json"
    if not raw_path.exists():
        print(f"[错误] 找不到提取结果: {raw_path}")
        sys.exit(1)

    try:
        ground_truths = load_ground_truth(xlsx_path, require_reviewed=not allow_unreviewed)
    except ValueError as e:
        print(f"[错误] {e}")
        sys.exit(1)
    predictions = load_predictions(raw_path)

    gt_ids = {g["announcement_id"] for g in ground_truths}
    print(f"[评测] 验证集公告 {len(gt_ids)} 篇，"
          f"标准标的物 {sum(len(g['entities']) for g in ground_truths)} 条")
    print(f"[评测] 提取结果共 {len(predictions)} 篇，将在验证集范围内比对")

    metrics = Evaluator().evaluate(predictions, ground_truths)
    Evaluator().print_report(metrics)

    # 落盘，便于技术路线对比报告引用
    out = config.OUTPUT_DIR / "evaluation_report.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump({
            "accuracy": metrics.accuracy,
            "precision": metrics.precision,
            "recall": metrics.recall,
            "f1_score": metrics.f1_score,
            "composite": round(metrics.accuracy * 0.4 + metrics.precision * 0.3
                               + metrics.recall * 0.3, 4),
            "total_samples": metrics.total_samples,
            "total_fields": metrics.total_fields,
            "correct_fields": metrics.correct_fields,
            "field_stats": metrics.field_stats,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n[输出] 评测报告已写入: {out}")


if __name__ == "__main__":
    main()
