"""Independent read-only OOXML check of saved candidate; no Excel authoring."""
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import re
import sys
from zipfile import ZipFile
import xml.etree.ElementTree as ET

from standard_export import require, encoded

here = Path(__file__).resolve().parent
directory = here / sys.argv[1]
require(directory.resolve().is_relative_to(here), "outside V13")
data = json.loads((directory / "tables.json").read_text(encoding="utf-8"))
file = directory / "任务一-V13三表候选.xlsx"
ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
numeric = {"单价", "总价", "中标金额", "报价（万元）", "得分", "序号"}
verified = {}
with ZipFile(file) as z:
    strings = []
    if "xl/sharedStrings.xml" in z.namelist():
        strings = ["".join(si.itertext()) for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall("s:si", ns)]
    workbook = ET.fromstring(z.read("xl/workbook.xml"))
    sheets = list(workbook.find("s:sheets", ns))
    require([s.attrib["name"] for s in sheets] == ["总表", "标的表", "投标人表"], "wrong sheets")
    relations = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    targets = {r.attrib["Id"]: r.attrib["Target"] for r in relations}
    styles = ET.fromstring(z.read("xl/styles.xml"))
    fills = list(styles.find("s:fills", ns)); xfs = list(styles.find("s:cellXfs", ns))
    def col_index(address):
        total = 0
        for char in re.match("[A-Z]+", address)[0]: total = total * 26 + ord(char) - 64
        return total - 1
    def value(cell):
        kind = cell.attrib.get("t")
        if kind == "inlineStr": return "".join(cell.find("s:is", ns).itertext())
        raw = cell.findtext("s:v", namespaces=ns)
        if raw is None: return None
        if kind == "s": return strings[int(raw)]
        return raw
    for sheet in sheets:
        name = sheet.attrib["name"]
        rid = sheet.attrib["{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"]
        target = targets[rid]; member = target.lstrip("/") if target.startswith("/") else "xl/" + target
        root = ET.fromstring(z.read(member))
        require(not root.findall(".//s:f", ns), "unexpected executable formula")
        rows = list(root.find("s:sheetData", ns))
        headers = data["headers"][name]
        require([value(c) for c in rows[0]] == headers, "header mismatch")
        require(len(rows) - 1 == len(data["tables"][name]), "saved row count mismatch")
        for cell in rows[0]:
            label = headers[col_index(cell.attrib["r"])]
            style = xfs[int(cell.attrib.get("s", "0"))]
            color = fills[int(style.attrib["fillId"])].find("s:patternFill/s:fgColor", ns).attrib.get("rgb", "")[-6:]
            require(color == ("A6A6A6" if label in data["optional_headers"][name] else "1F4E79"), "header color mismatch")
        numeric_cells = 0
        for actual, expected in zip(rows[1:], data["tables"][name]):
            cells = {col_index(c.attrib["r"]): c for c in actual}
            for i, label in enumerate(headers):
                raw = expected.get(label, "")
                cell = cells.get(i)
                saved = value(cell) if cell is not None else None
                if raw in ("", None):
                    require(saved in ("", None), "missing was converted to a value")
                    continue
                text = str(raw)
                typed = bool(re.fullmatch(r"\d+(?:\.\d+)?", text)) and (label in numeric or label == "数量" and text.isdigit())
                if typed:
                    require(cell is not None and cell.attrib.get("t", "n") == "n", "numeric saved as text")
                    require(Decimal(saved) == Decimal(text), "numeric precision lost")
                    numeric_cells += 1
                else:
                    if re.match(r"[=+@-]", text):
                        require(cell is not None and cell.attrib.get("t") in {"s", "inlineStr", "str"}, "external text not saved as literal string")
                        require(saved == text, "external text changed")
                    else:
                        require(saved == text, "text value changed")
        verified[name] = {"rows": len(rows) - 1, "all_saved_cells_verified": True, "typed_numeric_cells": numeric_cells}
report = {"passed": True, "workbook_sha256": hashlib.sha256(file.read_bytes()).hexdigest(), "sheets": verified,
          "all_headers_present": True, "numeric_values_equal_exact_decimal": True, "formulas": 0}
with (directory / "saved-workbook-verification.json").open("xb") as stream: stream.write(encoded(report))
print(json.dumps(report, ensure_ascii=False))
