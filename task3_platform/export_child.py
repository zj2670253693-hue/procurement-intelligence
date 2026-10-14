"""Portable OOXML authoring; executed only in an isolated offline process."""
import csv
import io
import os
from pathlib import Path
import re
import socket
import sys
import unicodedata
import zipfile
from xml.sax.saxutils import escape

from .contracts import PlatformError, canonical_bytes, strict_json_loads
from .exports import NUMERIC_HEADERS, verify_workbook
from .paths import reject_link_ancestors
from .uploads import _hash_file

OUTPUT_LIMIT = 512 * 1024**2


def _check_budget(path, size):
    used = sum(item.stat().st_size for item in path.parent.iterdir() if item.is_file() and item != path)
    if used + size > OUTPUT_LIMIT:
        raise PlatformError("export_output_limit", 413)


class _BoundedFile:
    def __init__(self, target, path):
        self.target, self.path, self.length = target, path, 0

    def write(self, data):
        new_size = max(self.length, self.target.tell() + len(data))
        _check_budget(self.path, new_size)
        result = self.target.write(data)
        self.length = new_size
        return result

    def __getattr__(self, name):
        return getattr(self.target, name)


def _write(path, data):
    reject_link_ancestors(path)
    if path.exists():
        if path.read_bytes() != data:
            raise PlatformError("export_output_conflict", 409)
        return
    temporary = path.with_suffix(path.suffix + ".tmp")
    _check_budget(temporary, len(data))
    with open(temporary, "wb") as target:
        target.write(data)
        target.flush()
        os.fsync(target.fileno())
    os.replace(temporary, path)


def _column(index):
    result = ""
    while index:
        index, rest = divmod(index - 1, 26)
        result = chr(65 + rest) + result
    return result


def _sheet(sheet, rows, headers, optional):
    yield b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" state="frozen"/></sheetView></sheetViews><cols>'
    for index in range(1, len(headers) + 1):
        yield f'<col min="{index}" max="{index}" width="24" customWidth="1"/>'.encode()
    yield b'</cols><sheetData>'
    for row_no, values in enumerate([dict(zip(headers, headers)), *rows], 1):
        yield f'<row r="{row_no}">'.encode()
        for index, header in enumerate(headers, 1):
            value = str(values.get(header, ""))
            if len(value.encode("utf-16-le")) // 2 > 32767 or any(ord(char) < 32 and char not in "\t\n\r" for char in value):
                raise PlatformError("workbook_cell_limit", 413)
            reference = _column(index) + str(row_no)
            numeric = row_no != 1 and bool(re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value)) and (header in NUMERIC_HEADERS or header == "数量" and value.isascii() and value.isdecimal())
            style = "2" if row_no == 1 and header in optional else ("1" if row_no == 1 else "0")
            if numeric:
                yield f'<c r="{reference}" s="{style}"><v>{value}</v></c>'.encode()
            else:
                yield f'<c r="{reference}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{escape(value)}</t></is></c>'.encode("utf-8")
        yield b'</row>'
    yield b'</sheetData></worksheet>'


def _workbook(path, bundle, headers, optional):
    temporary = path.with_suffix(".xlsx.tmp")
    names = ["总表", "标的表", "投标人表"]
    metadata = {
      "[Content_Types].xml": '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' + ''.join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>' for i in range(1, 4)) + '</Types>',
      "_rels/.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
      "xl/workbook.xml": '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>' + ''.join(f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i, name in enumerate(names, 1)) + '</sheets></workbook>',
      "xl/_rels/workbook.xml.rels": '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">' + ''.join(f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>' for i in range(1, 4)) + '<Relationship Id="rId4" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/></Relationships>',
      "xl/styles.xml": '<?xml version="1.0"?><styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="11"/><name val="Calibri"/></font><font><b/><color rgb="FFFFFFFF"/><sz val="11"/><name val="Calibri"/></font></fonts><fills count="4"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FF1F4E79"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFA6A6A6"/></patternFill></fill></fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf/></cellStyleXfs><cellXfs count="3"><xf fontId="0" fillId="0" borderId="0" numFmtId="0" xfId="0"/><xf fontId="1" fillId="2" borderId="0" numFmtId="0" xfId="0" applyFill="1" applyFont="1"/><xf fontId="1" fillId="3" borderId="0" numFmtId="0" xfId="0" applyFill="1" applyFont="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>'}
    with open(temporary, "wb") as raw_target, zipfile.ZipFile(_BoundedFile(raw_target, temporary), "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in metadata.items():
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, value.encode("utf-8"))
        for index, name in enumerate(names, 1):
            if len(bundle["tables"][name]) > 1048575:
                raise PlatformError("workbook_row_limit", 413)
            info = zipfile.ZipInfo(f"xl/worksheets/sheet{index}.xml", (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            with archive.open(info, "w") as target:
                for chunk in _sheet(name, bundle["tables"][name], headers[name], optional[name]):
                    target.write(chunk)
    if path.exists():
        if _hash_file(path) != _hash_file(temporary):
            raise PlatformError("export_output_conflict", 409)
        temporary.unlink()
    else:
        temporary.rename(path)


def _source_prices(aid, source, html):
    from data_loader.html_parser import HTMLAnnouncementParser
    from extractor.summary_rules import build_summary_source
    from extractor.supplier_rules import _scope, _key
    from extractor.official_qa_money import _matrix_parts
    import price_projection
    content = HTMLAnnouncementParser().parse_html(html.decode("utf-8-sig"), aid)
    _, tables, valid = _scope(build_summary_source(content.html_text, content.html_tables))
    parts = [part for table in tables for part in _matrix_parts(table)] if valid else []
    prices = {}
    for binding in source["item_bindings"]:
        proof = binding.get("source_identity", {})
        if proof.get("proved") is not True:
            continue
        ti, ri = proof["source_table"], proof["source_row"]
        if ti >= len(parts) or ri >= len(parts[ti]):
            continue
        table, row = parts[ti], parts[ti][ri]
        columns = {}
        for field, pattern in price_projection.PRICE_HEADERS.items():
            indexes = [index for index, heading in enumerate(table[0]) if pattern.fullmatch(_key(heading))]
            if len(indexes) == 1 and indexes[0] < len(row):
                index = indexes[0]
                columns[field] = {"header": table[0][index], "raw_value": row[index], "cell": index}
        prices[(ti, ri)] = columns
    return {"source": source, "source_sha256": _hash_digest(html), "prices": prices}


def _hash_digest(data):
    import hashlib
    return hashlib.sha256(data).hexdigest()


def main():
    global OUTPUT_LIMIT
    def deny(*args, **kwargs):
        raise RuntimeError("export_network_forbidden")
    socket.create_connection = deny
    socket.socket.connect = deny
    socket.socket.connect_ex = deny
    request = strict_json_loads(Path(sys.argv[1]).read_bytes())
    OUTPUT_LIMIT = request["max_output_bytes"]
    tools_root = Path(__file__).resolve().parents[1] / "tools/task1_standard_export"
    sys.path.insert(0, str(tools_root))
    import standard_export, price_projection
    raw_path = Path(request["raw_path"])
    all_records = strict_json_loads(raw_path.read_bytes())
    if not isinstance(all_records, list):
        raise PlatformError("export_raw_schema")
    records = [record for record in all_records if record.get("success") is True and not record.get("validated_only")]
    sources, html_by_id = {}, {}
    for source in request["sources"]:
        html = Path(source["html_path"]).read_bytes()
        if _hash_digest(html) != source["html_sha"]:
            raise PlatformError("export_source_integrity", 409)
        aid = source["announcement_id"]
        html_by_id[aid] = html
        sources[aid] = standard_export.parse_source(html, aid)
    bundle = standard_export.project(records, sources)
    bundle, price_changes = price_projection.project_prices(bundle, records, bundle["name_metadata_joins"],
                         lambda aid: _source_prices(aid, sources[aid], html_by_id[aid]))
    for row in bundle["tables"]["标的表"]:
        row["证据来源"] = row["证据来源"].replace("V12基础结果；未逐项核实", "本批上传抽取结果；未逐项核实")
    destination = Path(request["destination"])
    _write(destination / "raw_results.json", raw_path.read_bytes())
    for name in standard_export.HEADERS:
        _write(destination / (name + ".csv"), standard_export.csv_bytes(name, bundle["tables"][name]))
    _workbook(destination / "standard.xlsx", bundle, standard_export.HEADERS, standard_export.OPTIONAL)
    verify_workbook(destination / "standard.xlsx", bundle["tables"], standard_export.HEADERS)
    successful = {record["announcement_id"] for record in records}
    errors = [{"announcement_id": source["announcement_id"], "code": "no_successful_extraction"}
              for source in request["sources"] if source["announcement_id"] not in successful and request["mode"] == "extract"]
    for record in all_records:
        aid = record.get("announcement_id")
        if aid not in sources:
            raise PlatformError("export_unknown_announcement", 409)
        for diagnostic in record.get("diagnostics", []):
            code = diagnostic.get("code") if isinstance(diagnostic, dict) else diagnostic
            if isinstance(code, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,127}", code):
                errors.append({"announcement_id": aid, "code": code})
    _write(destination / "errors.json", canonical_bytes(errors))
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=("announcement_id", "code"))
    writer.writeheader()
    writer.writerows(errors)
    _write(destination / "errors.csv", output.getvalue().encode("utf-8-sig"))
    projection = {"headers": standard_export.HEADERS, **bundle, "price_changes": price_changes}
    _write(destination / "standard_projection.json", canonical_bytes(projection))
    report = {"task_id": request["task_id"], "mode": request["mode"], "raw_sha256": _hash_file(raw_path),
              "source_hashes": {aid: _hash_digest(html) for aid, html in html_by_id.items()},
              "row_counts": {name: len(rows) for name, rows in bundle["tables"].items()},
              "input_count": len(request["sources"]), "successful_records": len(records),
              "incomplete": bool(errors), "saved_workbook_verified": True,
              "numeric_precision": "exact_decimal_text_in_original_OOXML", "excel_resave_precision_warning": "Excel may round beyond 15 significant digits; retain original outputs"}
    _write(destination / "export_report.json", canonical_bytes(report))


if __name__ == "__main__":
    main()
