"""Source-only summary compatibility for uploaded, non-CCGP HTML templates.

The frozen official projection requires a 公告信息 metadata table. Uploads can
publish the same information as labelled paragraphs instead. Restore ONLY the
missing summary fields and complete award-matrix rows; never trust extra_info,
join winners by order, calculate prices, or change the seven extracted fields.
"""
from copy import deepcopy
import re
import unicodedata

from bs4 import BeautifulSoup


VERSION = "uploaded-source-summary-v1"
PROJECT = {"项目名称", "采购项目名称"}
BUYER = {"采购单位", "采购人", "采购人名称"}
SECTION = re.compile(r"^(?:[一二三四五六七八九十0-9]+[、.．])?")


def _key(value):
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value or ""))


def _value(value):
    text = (value or "").strip()
    if (_key(text).lower() in {"", "/", "-", "无", "未公示", "未公布", "未提供", "未知", "null", "none", "n/a"}
            or re.match(r"^(?:详见|参见|见附件)", text)):
        return ""
    return text


def _metadata(html):
    soup = BeautifulSoup(html.decode("utf-8-sig"), "html.parser")
    for node in list(soup.select("script, style, noscript, template, [hidden], [aria-hidden='true']")):
        node.decompose()
    for node in list(soup.select("[style]")):
        if re.search(r"(?:display\s*:\s*none|visibility\s*:\s*hidden)", node.get("style", ""), re.I):
            node.decompose()
    # Same physical scope as the complete award-matrix parser below.
    root = soup.select_one(".vF_detail_content") or soup.select_one(".TRS_Editor") or soup.body or soup
    candidates = {"project_name": [], "procurement_unit": []}

    def add(label, value, evidence):
        value = _value(value)
        field = "project_name" if label in PROJECT else "procurement_unit" if label in BUYER else ""
        if field and value:
            candidates[field].append({"value": value, "evidence": evidence})

    for table in root.find_all("table"):
        for row in table.find_all("tr"):
            if row.find_parent("table") is not table:
                continue
            cells = row.find_all(["td", "th"], recursive=False)
            if len(cells) == 2 and not any(cell.find("table") for cell in cells):
                label = _key(cells[0].get_text("", strip=True)).rstrip(":")
                add(label, cells[1].get_text("", strip=True), "原文明确键值单元格：" + label)

    buyer_section = False
    for block in root.find_all(["p", "h1", "h2", "h3", "h4", "h5", "h6"]):
        # Do not treat nested table text or parent paragraph mirrors as headings.
        if block.find_parent(["table", "p"]) is not None:
            continue
        text = block.get_text("", strip=True)
        normalized = _key(text).rstrip(":")
        bare = SECTION.sub("", normalized)
        if bare in {"采购人信息", "采购单位信息"}:
            buyer_section = True
            continue
        if (block.name.startswith("h") or re.match(r"^[一二三四五六七八九十0-9]+[、.．]", normalized)
                or "采购代理机构信息" in bare):
            buyer_section = False
        pair = re.fullmatch(r"(?:[一二三四五六七八九十0-9]+[、.．])?([^:：]+)[:：](.*)", text)
        if pair:
            label, value = _key(pair[1]), pair[2]
            add(label, value, "原文明确标签：" + label)
            if buyer_section and label == "名称" and _value(value):
                add("采购人名称", value, "原文采购人信息下的名称")
    heading = soup.find("h1") or soup.find("h2") or soup.find("title")
    return (candidates, heading.get_text("", strip=True) if heading else "",
            root.get_text("\n", strip=True), str(soup).encode("utf-8"))


def restore_uploaded_summary(html, source):
    """Return a copy; retain all frozen item/bidder bindings and raw-source SHA."""
    result = deepcopy(source)
    candidates, title, text, visible_html = _metadata(html)
    audit = {"version": VERSION, "restored_fields": [], "award_rows_restored": 0, "conflicts": []}
    unique = {}
    for field, entries in candidates.items():
        by_value = {_key(entry["value"]): entry for entry in entries}
        if len(by_value) != 1:
            if len(by_value) > 1:
                audit["conflicts"].append(field)
            continue
        entry = next(iter(by_value.values()))
        existing = _value(result.get(field))
        if existing and _key(existing) != _key(entry["value"]):
            audit["conflicts"].append(field)
            continue
        unique[field] = entry["value"]
        if not existing:
            result[field] = entry["value"]
            audit["restored_fields"].append({"field": field, **entry})

    from tools.task1_standard_export.standard_export import classify
    if classify(title, text) == "intention_only":
        result["classification"] = "intention_only"
    # One visible project identity is required before bypassing the optional
    # mirror-table gate. Existing awards never get replaced or concatenated.
    if (unique.get("project_name") and not result.get("awards")
            and result.get("classification") != "intention_only"
            and not re.search(r"候选人|采购意向|废标|流标", title)):
        from tools.task1_standard_export.source_awards import parse_award_tables
        matrix = parse_award_tables(visible_html)
        if matrix["awards"]:
            if matrix["packages"] and re.search(r"未分包|不分包|不划分标段|不分标段", text):
                audit["conflicts"].append("divided")
            else:
                registered = {item["key"]: item for item in result.get("packages", [])}
                for package in matrix["packages"]:
                    registered.setdefault(package["key"], package)
                result["packages"] = list(registered.values())
                result["awards"] = matrix["awards"]
                if matrix["packages"]:
                    result["divided"] = True
                audit["award_rows_restored"] = len(matrix["awards"])
    result["uploaded_summary_recovery"] = audit
    return result
