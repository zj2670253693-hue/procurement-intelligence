"""Complete source-proven award matrix rows. Caller retains project-scope gate."""
import re
import unicodedata
from bs4 import BeautifulSoup, Tag, NavigableString


def key(text):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def package(text):
    normalized = key(text).rstrip(":")
    match = re.fullmatch(r"(?:(?:合同包|采购包|包)([0-9一二三四五六七八九十]+)|第([0-9一二三四五六七八九十]+)包)(?:\([^()]*\))?", normalized)
    if not match:
        return None
    number = match[1] or match[2]
    digits = {c: str(i) for i, c in enumerate("一二三四五六七八九十", 1)}
    number = digits.get(number, number.lstrip("0") or "0")
    return {"key": "包:" + number, "label": text.strip().rstrip(":：")}


def parse_award_tables(html):
    soup = BeautifulSoup(html.decode("utf-8-sig"), "html.parser")
    body = soup.select_one(".vF_detail_content") or soup.select_one(".TRS_Editor") or soup.body or soup
    role, context = False, None
    awards, packages, rejected = [], {}, []
    index = 0
    for node in body.descendants:
        text = None
        allow_caption = True
        if isinstance(node, Tag) and node.name in {"p", "h1", "h2", "h3", "h4"}:
            text = node.get_text("", strip=True)
        elif isinstance(node, NavigableString) and node.find_parent(["p", "h1", "h2", "h3", "h4"]) is None:
            text = str(node).strip()
            table = node.find_parent("table")
            allow_caption = table is None or table.find("table") is not None
        if text is not None:
            normalized = key(text).rstrip(":")
            heading = re.fullmatch(r"[一二三四五六七八九十0-9]+[、.．](.+)", normalized)
            if heading:
                role = heading[1] in {"采购结果", "中标(成交)信息", "中标信息", "成交信息", "中标结果", "成交结果"}
                context = None
            elif role and allow_caption and len(normalized) <= 240:
                caption = package(text)
                if caption:
                    context = caption; packages[caption["key"]] = caption
                elif re.match(r"(?:合同包|采购包|包|第[0-9一二三四五六七八九十]+包)", normalized):
                    context = None
                    rejected.append({"reason": "ambiguous_package_caption", "source_text": text})
                elif re.search(r"预算明细|最高限价|候选人|采购需求", normalized):
                    role = False; context = None
            continue
        if not isinstance(node, Tag) or node.name != "table":
            continue
        index += 1
        if not role:
            continue
        native = [r for r in node.find_all("tr") if r.find_parent("table") is node]
        cells = [[c for c in r.find_all(["td", "th"]) if c.find_parent("tr") is r] for r in native]
        if len(cells) < 2 or any(c.find("table") for row in cells for c in row):
            continue  # A parent mirror is not another physical source matrix.
        header = [key(c.get_text("", strip=True)) for c in cells[0]]
        owners = [i for i, h in enumerate(header) if h in {"供应商名称", "中标供应商", "成交供应商", "中标供应商名称"}]
        money = [(i, re.fullmatch(r"(?:中标(?:\(成交\))?|成交)(?:金额|价)(?:\((元|万元|亿元)\))?", h))
                 for i, h in enumerate(header)]
        money = [(i, match) for i, match in money if match]
        if len(owners) != 1 or len(money) != 1:
            continue
        if (len(set(header)) != len(header) or any(len(r) != len(header) for r in cells[1:])
                or any(int(c.get("rowspan", "1")) != 1 or int(c.get("colspan", "1")) != 1 for row in cells for c in row)):
            rejected.append({"table": index, "reason": "ragged_or_spanned_award_matrix"}); continue
        package_columns = [i for i, h in enumerate(header) if h in {"包号", "合同包号", "采购包", "合同包", "标包", "标段/合同包"}]
        for ri, row in enumerate(cells[1:], 1):
            owner = row[owners[0]].get_text("", strip=True)
            original = row[money[0][0]].get_text("", strip=True)
            value = re.sub(r"^报价:", "", key(original))
            value = re.sub(r"\((元|万元|亿元)\)", r"\1", value)
            amount = re.fullmatch(r"[¥￥]?((?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?)\s*(元|万元|亿元)?", value)
            declared = money[0][1][1] or ""
            missing_owner = key(owner).lower() in {"", "/", "-", "无", "未公示", "未公布", "未提供", "详见附件", "见附件", "null", "n/a"}
            if missing_owner or not amount or not (amount[2] or declared) or declared and amount[2] and declared != amount[2]:
                rejected.append({"table": index, "row": ri, "reason": "not_complete_explicit_currency_award"}); continue
            owned = context
            if len(package_columns) == 1:
                label = row[package_columns[0]].get_text("", strip=True)
                explicit = package(label if not re.fullmatch(r"\d+", label) else "包" + label)
                if explicit:
                    explicit["label"] = label
                    if context and context["key"] != explicit["key"]:
                        rejected.append({"table": index, "row": ri, "reason": "conflicting_package"}); continue
                    owned = explicit; packages[explicit["key"]] = explicit
                else:
                    owned = None  # A row's unknown package cannot borrow the preceding caption.
                    rejected.append({"table": index, "row": ri, "reason": "ambiguous_package_cell", "source_text": label})
            elif len(package_columns) > 1:
                rejected.append({"table": index, "row": ri, "reason": "multiple_package_columns"}); continue
            awards.append({"supplier": owner, "amount": amount[1] + (amount[2] or ""), "amount_unit": amount[2] or declared,
                           "package_key": owned["key"] if owned else "", "source_table": index, "source_row": ri,
                           "evidence": "actual_award_matrix"})
    return {"awards": awards, "packages": list(packages.values()), "rejected": rejected}
