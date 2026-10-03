"""
构建验证集（赛题建议：算法标注 + 人工核验）

流程：
  1. 从 1038 篇公告中分层抽样，保证覆盖不同标的物数量和附件情况
  2. 用当前提取结果作为「草稿标注」
  3. 附带原文摘录，便于人工核对
  4. 输出 Excel：人工直接在表里改，改完即可用于评测

用法：
  python build_validation_set.py ../benchmark_data/combined [抽样篇数]
"""
import json
import logging
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd

import config
from data_loader.attachment_parser import AttachmentParser
from data_loader.html_parser import HTMLAnnouncementParser

logging.getLogger("pdfplumber").setLevel(logging.ERROR)
logging.getLogger("pdfminer").setLevel(logging.ERROR)

OUTPUT_DIR = config.OUTPUT_DIR / "validation_set"

FIELDS = ["product_name", "category", "brand", "spec_model",
          "unit_price", "quantity", "total_price"]
FIELD_LABELS = [config.FIELD_LABELS[f] for f in FIELDS]


def stratify(records: list, n: int) -> list:
    """分层抽样：按标的物数量分档，按各档占比分配名额"""
    buckets = {"1-2条": [], "3-5条": [], "6-10条": [], "11条以上": []}
    for r in records:
        c = len(r.get("entities", []))
        if c <= 2:
            buckets["1-2条"].append(r)
        elif c <= 5:
            buckets["3-5条"].append(r)
        elif c <= 10:
            buckets["6-10条"].append(r)
        else:
            buckets["11条以上"].append(r)

    # 固定种子保证可复现，同时避免总是抽到文件列表最前面的公告。
    rng = random.Random(2026)
    for items in buckets.values():
        rng.shuffle(items)

    total = len(records)
    picked = []
    for name, items in buckets.items():
        quota = max(1, round(n * len(items) / total)) if items else 0
        quota = min(quota, len(items))
        picked.extend(items[:quota])
        print(f"  {name:10s} 共 {len(items):5d} 篇 → 抽 {quota} 篇")

    # 数量不足则用剩余样本补齐
    if len(picked) < n:
        chosen = {r["announcement_id"] for r in picked}
        for r in records:
            if len(picked) >= n:
                break
            if r["announcement_id"] not in chosen:
                picked.append(r)
    return picked[:n]


def main():
    html_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../benchmark_data/combined")
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 40

    raw_path = config.OUTPUT_DIR / "raw_results.json"
    with open(raw_path, "r", encoding="utf-8") as f:
        records = json.load(f)

    print(f"[验证集] 从 {len(records)} 篇公告中分层抽样 {n} 篇")
    sampled = stratify(records, n)

    html_parser = HTMLAnnouncementParser()
    att_parser = AttachmentParser()

    rows = []        # 待核验标注表
    references = []  # 原文摘录

    for i, r in enumerate(sampled, 1):
        aid = r["announcement_id"]
        entities = r.get("entities", [])

        # 待核验行：一篇公告的每个标的物一行
        if entities:
            for j, e in enumerate(entities, 1):
                row = {"公告ID": aid, "序号": j}
                for f, label in zip(FIELDS, FIELD_LABELS):
                    row[label] = e.get(f, "")
                row["核验结论"] = ""   # 人工填写：正确 / 错误 / 删除
                rows.append(row)
        else:
            rows.append({"公告ID": aid, "序号": 0,
                         **{label: "" for label in FIELD_LABELS},
                         "核验结论": "该公告无标的物"})

        # 原文摘录（供人工核对）
        html_file = html_dir / f"{aid}.html"
        text = ""
        if html_file.exists():
            try:
                content = html_parser.parse_file(str(html_file))
                text = content.html_text
            except Exception:
                pass
        zip_file = html_dir / f"{aid}.zip"
        if zip_file.exists():
            try:
                texts, _ = att_parser.parse_zip(str(zip_file), aid)
                for fname, t in texts.items():
                    text += f"\n\n--- 附件：{fname} ---\n{t}"
            except Exception:
                pass

        references.append({"公告ID": aid, "标的物数": len(entities),
                           "原文": text[:20000]})
        print(f"  [{i}/{len(sampled)}] {aid}  标的物 {len(entities)} 条  原文 {len(text)} 字")

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    xlsx_path = OUTPUT_DIR / "ground_truth.xlsx"

    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="标注(待核验)", index=False)
        pd.DataFrame(references).to_excel(writer, sheet_name="原文摘录", index=False)

    print("\n" + "=" * 60)
    print(f"  验证集已生成: {xlsx_path}")
    print(f"  待核验标注行数: {len(rows)}")
    print(f"  涉及公告数: {len(sampled)}")
    print("=" * 60)
    print("\n  人工核验步骤：")
    print("   1) 打开 ground_truth.xlsx 的「标注(待核验)」页")
    print("   2) 对照「原文摘录」页逐条核对 7 个字段，直接改单元格")
    print("   3) 多余的标的物行：在「核验结论」填 删除")
    print("   4) 漏掉的标的物：在表格末尾新增行，填上公告ID与字段")
    print("   5) 核验完成后运行: python run_evaluation.py")


if __name__ == "__main__":
    main()
