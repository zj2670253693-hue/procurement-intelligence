"""V12: prove item money at item scope, never copy a package award to a product."""
from collections import Counter
from dataclasses import dataclass
from decimal import Decimal, localcontext
import re

from extractor.awarded_rate_tiers import _package_ids
from extractor.item_total_evidence import _item_regions, _aligned, _catalog, _service_catalog
from extractor.supplier_rules import _SECTION, _body_sections, _key, _money, _name, _scope, _usable
from extractor.total_award_rules import _label, _literal_amount, _awards
from models.schemas import ExtractedEntity

OFFICIAL_QA_RULE_VERSION = "official-qa-20261011-v12-2"
NAMES = {"名称", "货物名称", "商品名称", "产品名称", "产品服务名称", "服务名称", "标的名称", "采购标的", "工程名称"}
SUPPLIERS = {"供应商", "供应商名称", "中标供应商", "成交供应商", "中标成交供应商名称"}
PRICE = re.compile(r"(?:货物|产品|服务)?单价(?:\((元|万元|亿元)\))?$")
QUANTITY = re.compile(r"(?:货物|产品|服务|采购)?数量(?:\([^()]+\))?$")
TOTAL = re.compile(r"(?:总价|总金额|合价|金额)(?:\((元|万元|亿元)\))?$")
NEGATIVE = re.compile(r"预算|最高限价|采购需求|招标需求|投标报价|评审报价|候选人|未中标|未成交|废标|流标|范本|模板")
MISSING = {"", "无", "未公示", "未公布", "暂无", "未提供", "不适用", "-", "/", "null", "n/a", "详见附件", "见附件"}
COUNT_UNITS = {"台", "套", "件", "个", "项", "辆", "部", "批", "份", "座", "箱", "盒", "本", "册", "次", "吨", "米", "平方米", "立方米"}
OWNER_LABEL = re.compile(r"(?:供应商名称|中标成交供应商名称|中标供应商名称|成交供应商名称):(?P<value>.*)$")
AMOUNT_LABEL = re.compile(r"(?:中标\(成交\)金额|中标成交金额|中标金额|成交金额|中标价|成交价)(?:\((元|万元|亿元)\))?:(.*)$")


def normalize_missing(entity):
    result = entity.model_copy(deep=True)
    for field in ("product_name", "category", "brand", "spec_model", "unit_price", "quantity", "total_price"):
        if _key(getattr(result, field)).lower() in MISSING:
            setattr(result, field, "")
    return result


def decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def multiply_item(price: str, quantity: str, header: str, quantity_header: str = "") -> str:
    amount = _money(price, header)
    qty = re.fullmatch(r"((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)(?:台|套|件|个|项|辆|部|批|份|座|箱|盒|本|册|次|吨|米|平方米|立方米)?", _key(quantity))
    if amount is None or not qty:
        return ""
    declared = re.search(r"\(([^()]+)\)$", _key(quantity_header))
    if declared:
        unit = declared[1]
        literal_unit = _key(quantity)[len(qty[1]):]
        if unit not in COUNT_UNITS or literal_unit and literal_unit != unit:
            return ""  # Unknown scale/duration or contradictory literal unit.
    number = Decimal(qty[1].replace(",", ""))
    if number <= 0 or amount < 0 or len(qty[1]) > 32 or len(_key(price)) > 64:
        return ""
    with localcontext() as context:
        context.prec = 80
        return decimal_text(amount * number) + "元"


def _flatten(rows):
    return [_key(c) for row in rows for c in row if _key(c)]


def _matrix_parts(table):
    """A real table may switch goods/service headers mid-table. No row guessing."""
    fixed = NAMES | SUPPLIERS | {"序号", "品目号", "品目名称", "品目", "类别", "品牌", "货物品牌", "产品品牌",
            "规格型号", "货物型号", "产品型号", "型号", "采购包", "包号", "合同包", "标包", "服务范围", "服务要求",
            "服务时间", "服务标准", "项目用途", "简要技术要求"}
    indexes = []
    for i, row in enumerate(table):
        normalized = [_key(c) for c in row]
        if (len(normalized) > 1 and any(c in NAMES for c in normalized)
                and all(c in fixed or PRICE.fullmatch(c) or QUANTITY.fullmatch(c) or TOTAL.fullmatch(c) for c in normalized)):
            indexes.append(i)
    if not indexes or indexes == [0]:
        return [table]
    # Prefix rows remain veto evidence, not silently discarded.
    parts = [table[:indexes[0]]] if indexes[0] else []
    return parts + [table[start:indexes[n + 1] if n + 1 < len(indexes) else len(table)] for n, start in enumerate(indexes)]


def _witness(table, regions):
    wanted = _flatten(table)
    if not wanted:
        return []
    matches = []
    for ri, region in enumerate(regions):
        cells = [_key(c) for line in region for c in line.split("|") if _key(c)]
        for i in range(len(cells) - len(wanted) + 1):
            if cells[i:i + len(wanted)] == wanted:
                matches.append((ri, i))
    return matches


def _award_money_lines(body):
    """Keep actual nested 1.中标结果, but stop at failed/bid/other sections."""
    active = False
    for line in body.splitlines():
        heading = _SECTION.fullmatch(_key(line))
        if heading:
            active = bool(re.fullmatch(r"(?:中标(?:\(成交\))?|成交)(?:信息|结果)|采购结果", heading[1].rstrip(":")))
        elif active:
            yield line


@dataclass(frozen=True)
class ItemRow:
    name: str
    owner: str
    brand: str
    spec: str
    quantity: str
    quantity_header: str
    price: str
    price_header: str
    total: str
    total_header: str
    packages: frozenset[str]
    table_index: int
    row_index: int
    proved: bool
    package_object: bool


def item_catalog(source_text):
    body, tables, valid = _scope(source_text)
    if not valid:
        return []
    tables = [part for table in tables for part in _matrix_parts(table)]
    regions = _item_regions(body)
    body_cells = [_key(c) for region in regions for line in region for c in line.split("|") if _key(c)]
    body_counts = Counter(body_cells)
    claims = [c for c in body_cells if ":" in c]
    serialized_counts = Counter()
    for table in tables:
        if table:
            indexes = [i for i, h in enumerate(table[0]) if _key(h) in NAMES]
            if len(indexes) == 1:
                serialized_counts.update(_name(r[indexes[0]]) for r in table[1:] if len(r) > indexes[0] and _name(r[indexes[0]]))
    project = _project_name(tables)
    declared_awards = _awards(body)
    explicit_owner_package = any(OWNER_LABEL.fullmatch(_key(line)) and _label(OWNER_LABEL.fullmatch(_key(line))["value"])[0]
                                 for role, lines in _body_sections(body) if role == "award" for line in lines)
    ambiguous_award_context = any(a.package == "invalid_package" or a.package == "conflicting_package" and explicit_owner_package
                                  for a in declared_awards)
    for role, lines in _body_sections(body):
        if role == "award" and sum(bool(AMOUNT_LABEL.fullmatch(_key(line))) for line in lines) > sum(bool(OWNER_LABEL.fullmatch(_key(line))) for line in lines):
            ambiguous_award_context = True
    nonabsolute_award = any(re.search(r"%|费率|折扣|下浮率|百分比", _key(line)) for line in _award_money_lines(body))
    allowed_pipe_rows = {tuple(_key(c) for c in r) for t in tables for r in t}
    partial_pipe_matrix = any("|" in line and tuple(_key(c) for c in line.split("|")) not in allowed_pipe_rows
                              for region in regions for line in region)
    rows, used = [], set()
    for ti, table in enumerate(tables):
        if not table or len(table) < 2:
            continue
        header = [_key(c) for c in table[0]]
        names = [i for i, h in enumerate(header) if h in NAMES]
        owners = [i for i, h in enumerate(header) if h in SUPPLIERS]
        brands = [i for i, h in enumerate(header) if h in {"品牌", "货物品牌", "产品品牌"}]
        specs = [i for i, h in enumerate(header) if h in {"规格型号", "货物型号", "产品型号", "型号"}]
        prices = [i for i, h in enumerate(header) if PRICE.fullmatch(h)]
        quantities = [i for i, h in enumerate(header) if QUANTITY.fullmatch(h)]
        totals = [i for i, h in enumerate(header) if TOTAL.fullmatch(h)]
        witnesses = _witness(table, regions)
        if (len(names) != 1 or not (prices or quantities or totals) or len(owners) > 1 or len(brands) > 1 or len(specs) > 1
                or len(prices) > 1 or len(quantities) > 1 or len(totals) > 1
                or len(set(header)) != len(header) or any(NEGATIVE.search(h) for h in header)
                or any(len(r) != len(header) for r in table[1:]) or len(witnesses) != 1):
            continue
        identity = (witnesses[0], tuple(header), tuple(tuple(_key(c) for c in r) for r in table[1:]))
        if identity in used:
            continue  # A serialized mirror is not another physical source row.
        used.add(identity)
        for ri, cells in enumerate(table[1:], 1):
            name = _name(cells[names[0]])
            if not name:
                continue
            owner = cells[owners[0]] if owners else ""
            packages = _package_ids(owner) | _package_ids(name)
            for i, h in enumerate(header):
                if h in {"包号", "采购包", "合同包", "标包"}:
                    packages |= _package_ids("采购包" + cells[i]) | _package_ids(cells[i])
            contexts = [ctx for region in regions for _, ctx, values in _aligned(tuple(header), region)
                        if values == tuple(_key(c) for c in cells)]
            conflict = any((ctx[0] and packages and ctx[0] not in packages)
                           or (ctx[1] and owner and _key(_label(owner)[1]) != ctx[1]) for ctx in contexts)
            category_echo = sum(_key(cells[i]) == name for i, h in enumerate(header) if h == "品目名称")
            owner_package, owner_company = _label(owner)
            unknown_claims = [c for c in claims if not (c == _key(owner) and owner_company and owner_package and owner_package not in {"invalid_package", "conflicting_package"})]
            proved = (body_counts[name] - category_echo == 1 and serialized_counts[name] == 1
                      and not any(name in c for c in unknown_claims) and not conflict
                      and not ambiguous_award_context and not partial_pipe_matrix
                      and (len(contexts) == 1 or not contexts and all(cells))
                      and len(packages) <= 1)
            if nonabsolute_award and prices and _money(cells[prices[0]]) is None:
                # Generic website 单价(元) headers can repeat a percentage quote.
                # With contradictory rate context, a bare number is not CNY proof.
                proved = False
            relevant = [a for a in declared_awards if a.package in packages]
            if relevant and any(a.supplier and a.supplier != owner_company for a in relevant):
                proved = False
            if (not relevant and packages and owner_company
                    and any(a.supplier == owner_company and a.package and a.package not in {"invalid_package", "conflicting_package"}
                            and a.package not in packages for a in declared_awards)):
                proved = False
            technical = [cells[i] for i, h in enumerate(header) if PRICE.fullmatch(h) or QUANTITY.fullmatch(h) or h in {"规格型号", "货物型号", "产品型号", "型号"}]
            package_object = name == _key(project) or bool(packages and technical and all(not _usable(v) for v in technical))
            rows.append(ItemRow(name, owner, cells[brands[0]] if brands else _label(owner)[1],
                                cells[specs[0]] if specs else "", cells[quantities[0]] if quantities else "",
                                header[quantities[0]] if quantities else "",
                                cells[prices[0]] if prices else "", header[prices[0]] if prices else "",
                                cells[totals[0]] if totals else "", header[totals[0]] if totals else "",
                                frozenset(packages), ti, ri, proved, package_object))
    return rows


def base_money_evidence(entities, source_text):
    """Proof for the final amount, separate from project/package award roles."""
    catalog = item_catalog(source_text)
    literals = _catalog(source_text) + _service_catalog(source_text)
    counts = Counter(_name(e.product_name) for e in entities)
    evidence = []
    for entity in entities:
        name = _name(entity.product_name)
        if counts[name] != 1:
            continue
        candidates = [r for r in literals if r.name == name and r.witnessed]
        if len(candidates) == 1 and _money(entity.total_price, candidates[0].header) == _money(candidates[0].value, candidates[0].header):
            evidence.append({"scope": "item", "method": "explicit_item_total", "product_name": entity.product_name,
                             "source_value": candidates[0].value, "source_header": candidates[0].header, "after": entity.total_price})
            continue
        rows = [r for r in catalog if r.name == name]
        if len(rows) == 1 and rows[0].proved and not rows[0].package_object and not _usable(rows[0].total):
            row = rows[0]
            amount = multiply_item(row.price, row.quantity, row.price_header, row.quantity_header)
            if amount and _money(amount) == _money(entity.total_price):
                evidence.append({"scope": "item", "method": "unit_times_quantity", "product_name": entity.product_name,
                                 "unit_price": row.price, "quantity": row.quantity, "source_header": row.price_header,
                                 "quantity_header": row.quantity_header,
                                 "source_table": row.table_index, "source_row": row.row_index, "after": entity.total_price})
    return evidence


def award_records(body):
    """One supplier/amount source block at a time; no independent list zip."""
    found = []
    for role, lines in _body_sections(body):
        if role != "award":
            continue
        starts = [i for i, line in enumerate(lines) if OWNER_LABEL.fullmatch(_key(line))]
        contexts, context = {}, frozenset()
        for i, line in enumerate(lines):
            if re.fullmatch(r"(?:第[0-9一二三四五六七八九十]+包|(?:采购包|合同包|包)[0-9一二三四五六七八九十]+)[:：]?", _key(line)):
                context = frozenset(_package_ids(line))
            if i in starts:
                contexts[i] = context
        for j, start in enumerate(starts):
            block = lines[start:starts[j + 1] if j + 1 < len(starts) else len(lines)]
            declared = OWNER_LABEL.fullmatch(_key(block[0]))["value"]
            raw = re.split(r"[:：]", block[0], maxsplit=1)[1].strip() if declared else (block[1].strip() if len(block) > 1 else "")
            package, company = _label(raw)
            contextual = contexts.get(start, frozenset())
            if len(contextual) > 1 or package and contextual and contextual != {package}:
                return []
            if not package and contextual:
                package = next(iter(contextual))
            amounts = []
            for i, line in enumerate(block):
                match = AMOUNT_LABEL.fullmatch(_key(line))
                if not match:
                    continue
                value = re.split(r"[:：]", line, maxsplit=1)[1].strip()
                if not value and i + 1 < len(block):
                    value = block[i + 1].strip()
                    if i + 2 < len(block) and _key(block[i + 2]) in {"元", "万元", "亿元"}:
                        value += block[i + 2].strip()
                amount = _literal_amount(value, match[1] or "")
                amounts.append(amount)
            if not company or len(amounts) != 1 or not amounts[0] or package in {"invalid_package", "conflicting_package"}:
                return []  # Partial source blocks cannot silently disappear.
            found.append({"supplier": company, "package": package, "amount": amounts[0], "source_block": j})
    return found


def _project_name(tables):
    values = [row[1].strip() for table in tables for row in table if len(row) == 2 and _key(row[0]) == "采购项目名称" and _usable(row[1])]
    return values[0] if values and len({_key(v) for v in values}) == 1 else ""


def master_award_rows(source_text):
    body, tables, valid = _scope(source_text)
    project = _project_name(tables)
    awards = award_records(body) if valid else []
    if not project or not awards or any(a["package"] for a in awards):
        return None
    tables = [part for table in tables for part in _matrix_parts(table)]
    regions = _item_regions(body)
    candidates = []
    for table in tables:
        if not table or len(table) < 2:
            continue
        header = [_key(c) for c in table[0]]
        names = [i for i, h in enumerate(header) if h in NAMES | {"服务内容"}]
        owned_columns = any(h in SUPPLIERS | {"品牌", "货物品牌", "产品品牌"} or PRICE.fullmatch(h) or TOTAL.fullmatch(h)
                            for h in header)
        witnessed_items = owned_columns and len(_witness(table, regions)) == 1
        # Negative evidence only: HTML inline formatting can split addresses
        # or 450元/处 into native-text fragments. Never use joined text as
        # positive row/currency/ownership proof or to manufacture an amount.
        joined_witness = "".join(_flatten(table)) in _key(body).replace("|", "")
        explicit_award_columns = (any(re.fullmatch(r"(?:中标|成交)(?:单位|供应商)名称", h) for h in header)
                                  and any(re.fullmatch(r"(?:中标|成交)(?:金额|价)(?:\((?:元|万元|亿元)\))?", h) for h in header)
                                  and joined_witness)
        if (witnessed_items or explicit_award_columns) and not any(NEGATIVE.search(h) for h in header) and any(
                len(row) > i and _usable(row[i]) for row in table[1:] for i in names):
            return None  # Another actual named table outranks a blank template.
    for table in tables:
        if not table or len(table) < 2 or len(_witness(table, regions)) != 1:
            continue
        header = [_key(c) for c in table[0]]
        names = [i for i, h in enumerate(header) if h in NAMES]
        suppliers = [i for i, h in enumerate(header) if h in SUPPLIERS]
        totals = [i for i, h in enumerate(header) if TOTAL.fullmatch(h)]
        if len(names) != 1 or len(suppliers) != 1 or len(totals) != 1 or any(NEGATIVE.search(h) for h in header):
            continue
        if any(len(r) != len(header) or _usable(r[names[0]]) for r in table[1:]):
            continue
        pairs = [(r[suppliers[0]].strip(), _money(r[totals[0]], header[totals[0]])) for r in table[1:]]
        if Counter((_key(s), p) for s, p in pairs) == Counter((_key(a["supplier"]), _money(a["amount"])) for a in awards):
            candidates.append(pairs)
    if len(candidates) != 1:
        return None
    # Keep actual award occurrence multiplicity, including same supplier/price.
    from extractor.announcement_category import fill_announcement_category_entities
    records = [ExtractedEntity(product_name=project, brand=a["supplier"], total_price=a["amount"]) for a in awards]
    return fill_announcement_category_entities(records, source_text=source_text)


def revise_base_entities(entities, *, source_text, originals=None):
    master = master_award_rows(source_text)
    if master is not None:
        return master
    body, _, _ = _scope(source_text)
    awards = award_records(body)
    award_values = {_money(a["amount"]) for a in awards}
    catalog = item_catalog(source_text)
    literal_catalog = _catalog(source_text) + _service_catalog(source_text)
    original_counts = Counter(_name(e.product_name) for e in (originals if originals is not None else entities))
    result = []
    for original in entities:
        entity = normalize_missing(original)
        matches = [r for r in catalog if r.name == _name(entity.product_name)]
        literal_matches = [r for r in literal_catalog if r.name == _name(entity.product_name)]
        if (len(literal_matches) == 1 and original_counts[_name(entity.product_name)] == 1):
            item = literal_matches[0]
            packages = _package_ids(entity.product_name)
            if (item.witnessed and (not packages or packages == {item.package})
                    and not (item.package.endswith("标段") and _usable(entity.spec_model) and not _usable(item.spec))
                    and (not _usable(entity.brand) or _key(entity.brand) in item.brands)
                    and (not _usable(entity.spec_model) or not _usable(item.spec) or _key(entity.spec_model) == item.spec)):
                value = _money(item.value, item.header)
                existing = _money(entity.total_price) or _money(entity.total_price, item.header)
                if value is not None:
                    if existing != value:
                        match = TOTAL.fullmatch(item.header)
                        entity.total_price = _literal_amount(item.value, (match[1] or "") if match else "")
                    result.append(entity)
                    continue
        if not matches:
            result.append(entity)
            continue
        packages = _package_ids(entity.product_name)
        selected = [r for r in matches if (not packages or r.packages == packages)
                    and (not _usable(entity.brand) or _key(entity.brand) == _key(r.brand)
                         or not _usable(r.brand) and _key(entity.brand) == _key(_label(r.owner)[1]))
                    and (not _usable(entity.spec_model) or not _usable(r.spec) or _key(entity.spec_model) == _key(r.spec))]
        if any(r.package_object for r in selected):
            result.append(entity)
            continue
        if len(selected) == 1 and (len(matches) == 1 or packages) and original_counts[_name(entity.product_name)] == 1:
            row = selected[0]
            # An unsupported explicit total is not silently bypassed using
            # arithmetic; only absent totals and complete identity proof allow it.
            computed = multiply_item(row.price, row.quantity, row.price_header, row.quantity_header) if row.proved and not _usable(row.total) else ""
            if computed:
                entity.total_price = computed
            elif _money(entity.total_price) in award_values:
                entity.total_price = ""
        elif _money(entity.total_price) in award_values:
            entity.total_price = ""
        result.append(entity)
    return result
