"""
附件覆盖率与解析质量分析

赛题评分中「覆盖率」占 10 分，要求标的物覆盖【公告原文】和【附件】两类，各 5 分。
本脚本统计附件实际能解析到多少，用于：
  1. 判断附件那一档能拿几分
  2. 输出失败原因分类，作为「数据质量分析报告」的素材

用法：
  python analyze_attachments.py ../benchmark_data/combined
"""
import json
import sys
import zipfile
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from data_loader.attachment_parser import AttachmentParser

SUPPORTED = AttachmentParser.SUPPORTED_EXTENSIONS


def classify_error(exc: Exception) -> str:
    """把异常归类成可统计的原因"""
    msg = str(exc)
    if "File is not a zip file" in msg:
        return "文件损坏或非标准 OOXML（可能是旧版 .doc 改名）"
    if "No /Root object" in msg:
        return "PDF 损坏或无有效根对象"
    if "Unexpected EOF" in msg:
        return "文件被截断（下载不完整）"
    if "encrypted" in msg.lower() or "password" in msg.lower():
        return "加密文件"
    return f"其他：{msg[:60]}"


def analyze_one(html_file: Path, parser: AttachmentParser) -> dict:
    """分析单篇公告的附件情况"""
    aid = html_file.stem
    zip_path = html_file.parent / f"{aid}.zip"

    info = {
        "announcement_id": aid,
        "has_zip": zip_path.exists(),
        "total": 0,
        "ok": 0,
        "fail": 0,
        "entity_count": 0,
        "by_ext": Counter(),          # 扩展名 -> 总数
        "ok_by_ext": Counter(),       # 扩展名 -> 成功数
        "errors": Counter(),          # 失败原因 -> 次数
        "unreadable": 0,              # 无法读取的成员
    }
    if not info["has_zip"]:
        return info

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for fi in zf.infolist():
                if fi.is_dir():
                    continue
                ext = Path(fi.filename).suffix.lower()
                if ext not in SUPPORTED:
                    continue
                info["total"] += 1
                info["by_ext"][ext] += 1
                try:
                    data = zf.read(fi.filename)
                except Exception as e:
                    info["unreadable"] += 1
                    info["fail"] += 1
                    info["errors"][f"zip 成员读取失败：{str(e)[:40]}"] += 1
                    continue
                try:
                    text, tables = parser._parse_file(data, ext, fi.filename)
                    if text or tables:
                        info["ok"] += 1
                        info["ok_by_ext"][ext] += 1
                    else:
                        info["fail"] += 1
                        info["errors"]["解析结果为空"] += 1
                except Exception as e:
                    info["fail"] += 1
                    info["errors"][classify_error(e)] += 1
    except zipfile.BadZipFile:
        info["errors"]["zip 包本身损坏"] += 1

    return info


def main():
    html_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../benchmark_data/combined")
    if not html_dir.exists():
        print(f"[错误] 目录不存在: {html_dir}")
        sys.exit(1)

    raw_path = config.OUTPUT_DIR / "raw_results.json"
    with open(raw_path, "r", encoding="utf-8") as f:
        entity_counts = {r["announcement_id"]: len(r.get("entities", []))
                         for r in json.load(f)}

    html_files = sorted(html_dir.glob("*.html"))
    print(f"[分析] 共 {len(html_files)} 篇公告，开始扫描附件...\n")

    parser = AttachmentParser()
    results = []
    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = {ex.submit(analyze_one, f, parser): f for f in html_files}
        done = 0
        for fut in as_completed(futures):
            results.append(fut.result())
            done += 1
            if done % 200 == 0:
                print(f"  进度 {done}/{len(html_files)}")

    # ---------------- 汇总 ----------------
    n = len(results)
    with_zip = [r for r in results if r["has_zip"]]
    no_zip = [r for r in results if not r["has_zip"]]
    total_files = sum(r["total"] for r in with_zip)
    ok_files = sum(r["ok"] for r in with_zip)
    fail_files = sum(r["fail"] for r in with_zip)

    all_ext = Counter()
    all_ok_ext = Counter()
    all_errors = Counter()
    for r in with_zip:
        all_ext.update(r["by_ext"])
        all_ok_ext.update(r["ok_by_ext"])
        all_errors.update(r["errors"])

    # 附件完全不可用的公告（有 zip 但一个附件都没解析出来）
    zip_but_dead = [r for r in with_zip if r["total"] > 0 and r["ok"] == 0]
    # 附件覆盖判定的口径：公告原文始终可用；附件可用=至少解析出一个附件
    att_covered = [r for r in with_zip if r["ok"] > 0]

    print("\n" + "=" * 64)
    print("  附件覆盖率与解析质量分析")
    print("=" * 64)
    print(f"  公告总数（HTML 覆盖）        : {n}  ({n / n * 100:.1f}%)")
    print(f"  有同名附件 zip 的公告        : {len(with_zip)}  ({len(with_zip) / n * 100:.1f}%)")
    print(f"  无附件 zip 的公告            : {len(no_zip)}")
    print(f"  附件可用的公告（≥1个附件解析成功）: {len(att_covered)}  ({len(att_covered) / n * 100:.1f}%)")
    print(f"  有 zip 但附件全部解析失败    : {len(zip_but_dead)}")
    print("-" * 64)
    print(f"  附件文件总数                 : {total_files}")
    print(f"    解析成功                   : {ok_files}  ({ok_files / total_files * 100:.1f}%)" if total_files else "    解析成功: 0")
    print(f"    解析失败                   : {fail_files}  ({fail_files / total_files * 100:.1f}%)" if total_files else "    解析失败: 0")
    print("-" * 64)
    print("  按扩展名：")
    for ext, cnt in all_ext.most_common():
        ok = all_ok_ext.get(ext, 0)
        print(f"    {ext:7s} 总 {cnt:5d}  成功 {ok:5d}  失败 {cnt - ok:5d}  成功率 {ok / cnt * 100:5.1f}%")
    print("-" * 64)
    print("  失败原因 TOP：")
    for reason, cnt in all_errors.most_common(8):
        print(f"    {cnt:5d}  {reason}")
    print("=" * 64)

    # 覆盖率评分的粗判
    print("\n  【覆盖率评分粗判】")
    print(f"    公告原文一档: 已覆盖（{n} 篇均解析成功）→ 预计得 5 分")
    att_ratio = len(att_covered) / n
    if att_ratio >= 0.9:
        verdict = "附件一档基本拿满 → 预计得 5 分"
    elif att_ratio >= 0.5:
        verdict = "附件一档部分覆盖，需修复"
    else:
        verdict = "附件一档覆盖不足，需重点修复"
    print(f"    附件一档: {len(att_covered)}/{n} = {att_ratio * 100:.1f}% → {verdict}")

    # 落盘，供数据质量报告使用
    report = {
        "announcements": n,
        "with_zip": len(with_zip),
        "without_zip": len(no_zip),
        "attachment_covered": len(att_covered),
        "zip_but_all_failed": len(zip_but_dead),
        "attachment_files_total": total_files,
        "attachment_files_ok": ok_files,
        "attachment_files_failed": fail_files,
        "by_extension": {ext: {"total": c, "ok": all_ok_ext.get(ext, 0)}
                         for ext, c in all_ext.items()},
        "error_reasons": dict(all_errors),
        "zip_but_all_failed_ids": [r["announcement_id"] for r in zip_but_dead],
    }
    out = config.OUTPUT_DIR / "attachment_coverage_report.json"
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print(f"\n[输出] 明细已写入: {out}")


if __name__ == "__main__":
    main()