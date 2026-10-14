"""Conservative base-only header-unit restoration, never quantity inference."""

from dataclasses import dataclass
from decimal import Decimal
import re
import unicodedata

from extractor.supplier_rules import _aligned_rows, _body_sections, _key, _name, _package, _scope
from models.schemas import ExtractedEntity


QUANTITY_UNIT_RULE_VERSION = "explicit-quantity-header-unit-v1"
_NUMBER = r"\d+(?:\.\d+)?"
_BARE = re.compile(_NUMBER + r"$")
_MONEY_TOKEN = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_UNIT = r"台/套|台|套|项|个|件|张|人|人次|米|平方米|立方米|公斤|吨|辆|批|组|包|年|月|天|小时"
_QHEADER = re.compile(r"(?:货物数量|采购数量|数量)(?:\((?:单位:)?(" + _UNIT + r")\))?$")
_NAMES = {"名称", "服务名称", "货物名称", "产品名称", "采购标的", "标的名称"}
_SUPPLIERS = {"供应商名称", "中标供应商", "成交供应商"}
_PACKAGES = {"包号", "采购包", "标包"}
_MARKER = re.compile(r"^---[^\n]*---[ \t]*$", re.MULTILINE)
_ATTACHMENT = re.compile(r"---\s*附件[:：].+?\s*---$")
_PROJECT = re.compile(r"(?:[一二三四五六七八九十0-9]+[、.])?(?:采购)?项目名称[:|](.+)$")
_TEMPLATE = re.compile(r"报价表模板|投标文件格式|填写说明|样例|示例")
_MISSING = {"", "详见附件", "见附件", "详见招标文件", "详见附件招标文件", "未公示", "无", "-", "/"}


@dataclass(frozen=True)
class _Owner:
    name: str
    package: str
    supplier: str
    quantity: str


@dataclass(frozen=True)
class _Quantity:
    name: str
    package: str
    number: Decimal | None
    unit: str


def _number(value: str) -> Decimal | None:
    normalized = unicodedata.normalize("NFKC", value).strip()
    return Decimal(normalized) if _BARE.fullmatch(normalized) else None


def _column(header: list[str], labels: set[str]) -> int | None:
    indices = [i for i, cell in enumerate(header) if cell in labels]
    return indices[0] if len(indices) == 1 else None


def _row_package(header: list[str], cells: list[str], name: str, supplier: str = "") -> str:
    choices = set()
    if sum(cell in _PACKAGES for cell in header) > 1:
        return "invalid_package"
    pi = _column(header, _PACKAGES)
    if pi is not None:
        raw = _key(cells[pi])
        prefix = "标包:" if header[pi] == "标包" else "采购包"
        choices.add(_package(raw) or _package(prefix + raw) or "invalid_package")
    for label in re.findall(r"第[0-9一二三四五六七八九十]+包|采购包[0-9一二三四五六七八九十]+|标包:[A-Za-z0-9]+", name):
        choices.add(_package(label))
    prefix = re.match(r"^(第[0-9一二三四五六七八九十]+包)[-—:]", _key(supplier))
    if prefix:
        choices.add(_package(prefix[1]))
    return next(iter(choices)) if len(choices) == 1 else ("conflicting_package" if choices else "")


def _company(value: str) -> str:
    return re.sub(r"^第[0-9一二三四五六七八九十]+包[-—:]", "", _key(value))


def _body_tables(lines: list[str], headers: set[tuple[str, ...]]):
    """Split at every declared header so mixed service/goods columns cannot drift."""
    active, current, package, i = None, [], "", 0
    ordered = sorted(headers, key=len, reverse=True)
    while i < len(lines):
        if _package(lines[i]):
            package = lines[i]
        chosen, width = None, 0
        for header in ordered:
            if "|" in lines[i] and tuple(_key(cell) for cell in lines[i].split("|")) == header:
                chosen, width = header, 1
                break
            if tuple(_key(line) for line in lines[i:i + len(header)]) == header:
                chosen, width = header, len(header)
                break
        if chosen:
            if active:
                yield list(active), current
            active = chosen
            current = ([package] if package else []) + lines[i:i + width]
            i += width
        else:
            if active:
                current.append(lines[i])
            i += 1
    if active:
        yield list(active), current


def _owners(body: str, tables: list[list[list[str]]]) -> set[_Owner]:
    owners = set()
    headers = {tuple(_key(cell) for cell in row) for table in tables for row in table
               if len(row) > 1 and _column([_key(cell) for cell in row], _NAMES) is not None}
    for role, lines in _body_sections(body):
        if role != "item":
            continue
        for header, segment in _body_tables(lines, headers):
            ni = _column(header, _NAMES)
            for body_package, cells in _aligned_rows(header, segment):
                if ni is None or cells[ni] in _NAMES or not _name(cells[ni]):
                    continue
                si = _column(header, _SUPPLIERS)
                supplier = cells[si] if si is not None else ""
                qi = [i for i, h in enumerate(header) if _QHEADER.fullmatch(h)]
                quantity = cells[qi[0]] if len(qi) == 1 else ("ambiguous_quantity_columns" if qi else "")
                package = _row_package(header, list(cells), cells[ni], supplier)
                if body_package and package and body_package != package:
                    package = "conflicting_package"
                package = package if package in {"invalid_package", "conflicting_package"} else body_package or package
                owners.add(_Owner(_name(cells[ni]), package, supplier, quantity))
    # Serialized HTML can only add conservative negative evidence to a body-
    # proven owner; it cannot establish a detached item or package by itself.
    body_owners = set(owners)
    for table in tables:
        header = None
        for row in table:
            normalized = [_key(cell) for cell in row]
            if tuple(normalized) in headers:
                header = normalized
                continue
            if header is None or len(row) != len(header):
                continue
            ni, si = _column(header, _NAMES), _column(header, _SUPPLIERS)
            qi = [i for i, h in enumerate(header) if _QHEADER.fullmatch(h)]
            if ni is None or not qi:
                continue
            quantity = row[qi[0]] if len(qi) == 1 else "ambiguous_quantity_columns"
            if _key(quantity).rstrip("。;；，,") in _MISSING:
                continue
            for owner in body_owners:
                if _name(row[ni]) != owner.name:
                    continue
                serialized_package = _row_package(header, row, row[ni], row[si] if si is not None else "")
                if (serialized_package and owner.package and serialized_package != owner.package
                        and serialized_package not in {"invalid_package", "conflicting_package"}):
                    continue
                owners.add(_Owner(owner.name, owner.package, owner.supplier, quantity))
    return owners


def _pipe_quantities(text: str) -> set[_Quantity]:
    found, header = set(), None
    for line in text.splitlines():
        if "|" not in line:
            if line.strip():
                header = None
            continue
        cells = [cell.strip() for cell in line.split("|")]
        normalized = [_key(cell) for cell in cells]
        if _column(normalized, _NAMES) is not None:
            header = normalized
            continue
        if header is None:
            continue
        ni = _column(header, _NAMES)
        quantities = [i for i, h in enumerate(header) if _QHEADER.fullmatch(h)]
        if ni is None or not quantities or ni >= len(cells) or not _name(cells[ni]):
            continue
        if len(cells) != len(header) or len(quantities) != 1:
            found.add(_Quantity(_name(cells[ni]), "invalid_package", None, ""))
            continue
        qi = quantities[0]
        match = _QHEADER.fullmatch(header[qi])
        found.add(_Quantity(_name(cells[ni]), _row_package(header, cells, cells[ni]),
                            _number(cells[qi]), match[1] or ""))
    return found


def _wrapped_quantities(text: str) -> set[_Quantity]:
    """Recognize only a complete declared package/name/quantity budget layout.

    Prices are opaque row-boundary tokens, never quantity values. Unknown
    headers, cross-page continuations and non-package subitems are not mined.
    """
    lines = [unicodedata.normalize("NFKC", line).strip() for line in text.splitlines() if line.strip()]
    found = set()
    for start in range(len(lines) - 3):
        first = _key(lines[start])
        imported = first == "包数量预算单价预算金额是否允许进口"
        if not imported and first != "包数量预算单价预算金额":
            continue
        if _key(lines[start + 1]) != "标的名称":
            continue
        suffix = "产品投标" if imported else ""
        header = re.fullmatch(r"号\((" + _UNIT + r")\)\(元\)\(元\)" + suffix, _key(lines[start + 2]))
        if not header:
            continue
        row_pattern = re.compile(r"^(?P<package>[1-9]\d*)\s+(?:(?P<name>[^0-9|]+?)\s+)?"
                                 r"(?P<number>" + _NUMBER + r")\s+" + _MONEY_TOKEN + r"\s+" + _MONEY_TOKEN
                                 + (r"\s+(?:是|否)" if imported else "") + r"$")
        active, pending, local_found, ambiguous = None, [], set(), False

        def finish():
            if active:
                package, number, name = active
                full_name = _name("".join(name + pending))
                if full_name:
                    local_found.add(_Quantity(full_name, _package("采购包" + package), _number(number), header[1]))

        for line in lines[start + 3:]:
            match = row_pattern.fullmatch(line)
            if match:
                if active is not None and not match["name"]:
                    # Shared text between two unnamed numeric rows cannot be
                    # partitioned into names without guessing their row order.
                    ambiguous = True
                    break
                prefix = pending if active is None else []
                finish()
                active = (match["package"], match["number"], prefix + ([match["name"]] if match["name"] else []))
                pending = []
            elif (re.match(r"注[:：]|说明[:：]|\[PDF|第.+[章节]|合计|小计|^---", line)
                  or _BARE.fullmatch(line)
                  or re.search(_NUMBER + r"\s+" + _MONEY_TOKEN + r"\s+" + _MONEY_TOKEN + r"(?:\s+(?:是|否))?$", line)):
                finish()
                active, pending = None, []
                break
            else:
                pending.append(line)
        finish()
        if not ambiguous:
            found.update(local_found)
    return found


def _actual_regions(text: str) -> list[str]:
    regions, current, template = [], [], False
    for line in text.splitlines():
        key = _key(line)
        chapter = bool(re.match(r"第[0-9一二三四五六七八九十]+章", key))
        heading = chapter or bool(re.match(r"^[一二三四五六七八九十0-9]+[、.](?=\D)", key))
        title = re.sub(r"^\((?:模板|样例|示例)\)", "", key.strip(":|"))
        title = re.sub(r"(?:\((?:模板|样例|示例)\)|\|说明)$", "", title)
        if heading or _TEMPLATE.fullmatch(title):
            if current:
                regions.append("\n".join(current))
            # Numbered subsections cannot escape an enclosing template region.
            # Only a new explicitly declared chapter can re-establish reality.
            template = bool(_TEMPLATE.search(key)) or (template and not chapter)
            current = []
        if not template:
            current.append(line)
    if current:
        regions.append("\n".join(current))
    return regions


def _project_names(regions: list[str], project: str) -> list[str]:
    names = []
    for region in regions:
        lines = [_key(line) for line in region.splitlines() if line.strip()]
        for j, line in enumerate(lines):
            match = _PROJECT.fullmatch(line)
            if match:
                name, k = match[1], j + 1
            elif line.rstrip(":") in {"项目名称", "采购项目名称"}:
                name, k = (lines[j + 1] if j + 1 < len(lines) else ""), j + 2
            else:
                continue
            # Only exact concatenation to the known metadata name is allowed;
            # a partial prefix by itself is not proof of the same project.
            while name and name != project and project.startswith(name) and k < len(lines):
                if not project.startswith(name + lines[k]):
                    break
                name += lines[k]
                k += 1
            names.append(name)
    return names


def _attachments(source: str, project: str) -> set[_Quantity]:
    normalized = (source or "").replace("\r\n", "\n")
    markers = list(_MARKER.finditer(normalized))
    found = set()
    for i, marker in enumerate(markers):
        if not _ATTACHMENT.fullmatch(marker.group().strip()):
            continue
        end = next((m.start() for m in markers[i + 1:] if not re.fullmatch(r"---\s*HTML表格[1-9][0-9]*\s*---", m.group().strip())), len(normalized))
        text = normalized[marker.end():end]
        regions = _actual_regions(text)
        names = _project_names(regions, project)
        if any(name != project for name in names):
            continue
        for region in regions:
            found.update(_pipe_quantities(region))
            found.update(_wrapped_quantities(region))
    return found


def quantity_unit_decisions(entities: list[ExtractedEntity], *, source_text: str) -> list[tuple[str, str]]:
    """Return a literal unit and source reason; never use gold or row order."""
    body, tables, valid = _scope(source_text)
    owners = _owners(body, tables) if valid else set()
    projects = {_key(row[1]) for table in tables for row in table if len(row) == 2 and _key(row[0]) == "采购项目名称"}
    quantities = _attachments(source_text, next(iter(projects))) if valid and len(projects) == 1 else set()
    decisions = []
    for entity in entities:
        number = _number(entity.quantity)
        if number is None:
            decisions.append(("", "preserve_nonbare_quantity"))
            continue
        if not valid or len(projects) != 1:
            decisions.append(("", "ambiguous_announcement_scope"))
            continue
        matching = {o for o in owners if o.name == _name(entity.product_name) and o.name}
        if entity.brand.strip():
            matching = {o for o in matching if not o.supplier or _company(o.supplier) == _key(entity.brand)}
        if len(matching) != 1:
            decisions.append(("", "no_unique_html_item_owner"))
            continue
        owner = next(iter(matching))
        if _key(owner.quantity).rstrip("。;；，,") not in _MISSING:
            decisions.append(("", "preserve_html_declared_quantity"))
            continue
        if not owner.package or owner.package in {"invalid_package", "conflicting_package"}:
            decisions.append(("", "conflicting_item_package"))
            continue
        if any(q.name == owner.name and q.package in {"invalid_package", "conflicting_package"} for q in quantities):
            decisions.append(("", "no_unique_quantity_row"))
            continue
        candidates = {q for q in quantities if q.name == owner.name and q.package == owner.package}
        if len(candidates) != 1:
            decisions.append(("", "no_unique_quantity_row"))
            continue
        candidate = next(iter(candidates))
        unit = candidate.unit if candidate.number == number and candidate.unit else ""
        decisions.append((unit, "unique_quantity_header_unit" if unit else "quantity_number_or_unit_unproven"))
    return decisions


def restore_quantity_units(entities: list[ExtractedEntity], *, source_text: str) -> list[ExtractedEntity]:
    """Copy base rows after existing dedup, changing only the missing unit."""
    cleaned = []
    for original, (unit, _) in zip(entities, quantity_unit_decisions(entities, source_text=source_text)):
        entity = original.model_copy(deep=True)
        if unit:
            entity.quantity = original.quantity + "（" + unit + "）"
        cleaned.append(entity)
    return cleaned
