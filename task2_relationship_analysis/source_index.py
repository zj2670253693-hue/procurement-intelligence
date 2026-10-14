import codecs
import html
from html.parser import HTMLParser
import re
from .contracts import SourceIndex, Section, PhysicalCell, TableIndex
from .inputs import issue
from .serialization import sha256, make_record_id

LOT = re.compile(r"(?:合同包\s*[（(]?\s*\d+|第\s*[一二三四五六七八九十\d]+\s*包|[一二三四五六七八九十\d]+\s*标段)")
HEAD = re.compile(r"^(?:[一二三四五六七八九十]+[、.．]|\d+[、.．])|采购人信息|采购代理机构信息|评审|投标|候选人")

class _Parser(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=False)
        self.text = text
        self.offsets = [0]
        for match in re.finditer("\n", text):
            self.offsets.append(match.end())
        self.sections, self.tables, self.stack = [], [], []
        self.block = None
        self.block_tag = None
        self.cell = None
        self.ignored = 0
        self.title, self.lot = "", ""

    def position(self):
        line, column = self.getpos()
        return self.offsets[line - 1] + column

    def _finish_block(self):
        if self.block and self.block.text.strip():
            value = self.block.text.strip()
            if HEAD.search(value) and len(value) < 140:
                self.title = value
                if re.match(r"^[一二三四五六七八九十]+[、.．]", value):
                    self.lot = ""
            found = LOT.search(value)
            if found and len(value) < 140:
                self.lot = found.group()
            self.block.title = self.title + (" | " + self.lot if self.lot else "")
            self.sections.append(self.block)
        self.block, self.block_tag = None, None

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.ignored += 1
            return
        if self.ignored:
            return
        at = self.position()
        if tag == "table":
            self._finish_block()
            table = TableIndex(len(self.tables), at, at, [], self.title + (" | " + self.lot if self.lot else ""), bool(self.stack))
            if self.stack:
                for previous in self.stack:
                    previous.malformed = True
            self.tables.append(table)
            self.stack.append(table)
            self.cell = None
        elif tag == "tr" and self.stack:
            if self.cell:
                self.stack[-1].malformed = True
            self.cell = None
            self.stack[-1].rows.append([])
        elif tag in {"td", "th"} and self.stack:
            table = self.stack[-1]
            if not table.rows or self.cell:
                table.malformed = True
                if not table.rows:
                    table.rows.append([])
            try:
                values = dict(attrs)
                rs, cs = int(values.get("rowspan", 1)), int(values.get("colspan", 1))
                if not (1 <= rs <= 1000 and 1 <= cs <= 1000):
                    raise ValueError()
            except (ValueError, TypeError):
                table.malformed = True
                rs = cs = 1
            self.cell = PhysicalCell(at, at, "", table.heading, [], table.table, len(table.rows) - 1, len(table.rows[-1]), rs, cs, tag == "th")
            table.rows[-1].append(self.cell)
        elif not self.stack and tag in {"p", "h1", "h2", "h3", "h4", "h5", "h6", "li"}:
            self._finish_block()
            self.block = Section(at, at, "", self.title, [])
            self.block_tag = tag
        elif not self.stack and tag == "strong" and self.block is None:
            self.block = Section(at, at, "", self.title, [])
            self.block_tag = tag

    def handle_endtag(self, tag):
        if tag in {"script", "style"}:
            self.ignored = max(0, self.ignored - 1)
            return
        if self.ignored:
            return
        if tag in {"td", "th"} and self.stack:
            self.cell = None
        elif tag == "tr" and self.stack:
            if self.cell:
                self.stack[-1].malformed = True
            self.cell = None
        elif tag == "table" and self.stack:
            if self.cell:
                self.stack[-1].malformed = True
            self.stack[-1].end = self.position() + len("</table>")
            self.stack.pop()
            self.cell = None
        elif tag == self.block_tag:
            self._finish_block()

    def _append(self, value, positions):
        if self.ignored or not value:
            return
        region = self.cell if self.stack else self.block
        if region is None:
            if self.stack or not value.strip():
                return
            region = Section(positions[0][0], positions[-1][1], "", self.title, [])
            self.block, self.block_tag = region, "__text__"
        region.text += value
        region.positions.extend(positions)
        region.end = positions[-1][1]

    def handle_data(self, data):
        at = self.position()
        self._append(data, [(at + j, at + j + 1) for j in range(len(data))])

    def handle_entityref(self, name):
        raw = "&" + name + ";"
        at = self.position()
        value = html.unescape(raw)
        self._append(value, [(at, at + len(raw))] * len(value))

    def handle_charref(self, name):
        self.handle_entityref("#" + name)

def index_html(announcement_id: str, raw: bytes, dataset_id: str) -> SourceIndex:
    digest = sha256(raw)
    sid = make_record_id(dataset_id, "source", (announcement_id, digest))
    encoding = "utf-8-sig" if raw.startswith(codecs.BOM_UTF8) else "utf-8"
    declared = re.findall(br"charset\s*=\s*['\"]?\s*([a-zA-Z0-9_-]+)", raw[:8192], re.I)
    try:
        encodings = {codecs.lookup(item.decode("ascii")).name for item in declared}
        if len(encodings) > 1 or (raw.startswith(codecs.BOM_UTF8) and encodings and encodings != {"utf-8"}):
            raise UnicodeError("encoding conflict")
        if encodings and not raw.startswith(codecs.BOM_UTF8):
            encoding = next(iter(encodings))
        text = raw.decode(encoding, errors="strict")
    except (UnicodeError, LookupError):
        return SourceIndex(sid, announcement_id, digest, None, "", [], [], [issue(dataset_id, announcement_id, "decode_failed")], dataset_id)
    parser = _Parser(text)
    parser.feed(text)
    parser.close()
    parser._finish_block()
    for table in parser.stack:
        table.malformed = True
    diagnostics = [issue(dataset_id, announcement_id, "parse_failed", details={"table": t.table}) for t in parser.tables if t.malformed]
    return SourceIndex(sid, announcement_id, digest, encoding, text, parser.sections, parser.tables, diagnostics, dataset_id)
