"""
探测「解析结果为空」的 PDF 到底长什么样

假设：这些是扫描件/图片型 PDF，页面里没有文字层，只有图片，
      所以 pdfplumber 能打开、但不返回任何文本 —— 需要 OCR 才能提取。

用法：python probe_empty_pdf.py ../benchmark_data/combined [样本公告数]
"""
import io
import logging
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

logging.getLogger("pdfplumber").setLevel(logging.ERROR)
logging.getLogger("pdfminer").setLevel(logging.ERROR)

import pdfplumber

html_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../benchmark_data/combined")
limit_ann = int(sys.argv[2]) if len(sys.argv) > 2 else 60

zips = sorted(html_dir.glob("*.zip"))[:limit_ann]
found = []

for zp in zips:
    try:
        with zipfile.ZipFile(zp, "r") as zf:
            for fi in zf.infolist():
                if fi.is_dir() or not fi.filename.lower().endswith(".pdf"):
                    continue
                data = zf.read(fi.filename)
                with pdfplumber.open(io.BytesIO(data)) as pdf:
                    text = ""
                    n_img = 0
                    n_char = 0
                    for pg in pdf.pages[:5]:
                        text += pg.extract_text() or ""
                        n_img += len(pg.images)
                        n_char += len(pg.chars)
                    if not text.strip():
                        found.append({
                            "zip": zp.stem,
                            "file": fi.filename[:50],
                            "pages": len(pdf.pages),
                            "images(前5页)": n_img,
                            "chars(前5页)": n_char,
                        })
                if len(found) >= 15:
                    break
    except Exception:
        continue
    if len(found) >= 15:
        break

print("=" * 78)
print("  空文本 PDF 探测结果（样本）")
print("=" * 78)
if not found:
    print("  未找到空文本 PDF")
for f in found:
    judgement = "→ 图片型/扫描件（需 OCR）" if f["images(前5页)"] > 0 and f["chars(前5页)"] == 0 else "→ 需人工确认"
    print(f"  {f['zip']}  页数 {f['pages']:>3}  图片 {f['images(前5页)']:>3}  字符 {f['chars(前5页)']:>5}  {judgement}")
    print(f"      {f['file']}")
print("=" * 78)
print(f"  样本中空文本 PDF 共 {len(found)} 个（扫描了 {len(zips)} 个 zip 包）")