"""Source disclosure roles; first-stage intent never proves an actual trade."""
import html
import re

from .evidence import normalized
from .inputs import issue

_CONTEXT = re.compile(r'框架协议|入围|征集|采购意向|协议折扣')
_ORDER_TITLE = re.compile(r'第二阶段|订单(?:成交|结果)|实际成交|合同成交')
_FIRST_STAGE_TITLE = re.compile(
    r'采购意向(?:公告|公示|公开)(?:[（(][^）)]*[）)])?$'
    r'|入围(?:结果|公示|公告|供应商)|征集(?:结果|公告|公示)')
_NO_TRANSACTION = re.compile(
    r'尚无实际成交|(?:尚未|未)(?:发生)?实际(?:采购|成交)|仅(?:为)?采购意向|仅征集'
    r'|未公示具体成交|不代表实际成交')
_INTENT_BODY = re.compile(r'第一阶段|入围信息|入围供应商|入围单价|协议折扣')


def intention_only(index):
    """Require an announcement title/heading, not a phrase in an arbitrary cell.

    Explicit second-stage order titles remain eligible for normal source proof.
    A clear statement that no actual purchase happened still vetoes contradictory
    generic winning-supplier/amount headings. Visible text excludes script/style.
    """
    titles = []
    match = re.search(r'<title\b[^>]*>(.*?)</title\s*>', index.decoded_html, re.I | re.S)
    if match:
        titles.append(normalized(html.unescape(re.sub(r'<[^>]*>', '', match[1]))))
    titles.extend(normalized(section.text) for section in index.sections
        if re.match(r'<h[12]\b', index.decoded_html[section.start:section.start + 16], re.I))
    titles = [title for title in titles if _CONTEXT.search(title)]
    if not titles:
        return False
    chunks = [normalized(section.text) for section in index.sections] + [
        normalized(cell.text) for table in index.tables for row in table.rows for cell in row]
    actual_order_title = any(_ORDER_TITLE.search(title) for title in titles)
    for chunk in chunks:
        for marker in _NO_TRANSACTION.finditer(chunk):
            prefix = chunk[:marker.start()]
            if re.search(r'(?:不是|并非|不属于|非)$', prefix):
                continue
            clause = re.split(r'[。；;！？]', prefix)[-1]
            if actual_order_title and re.search(r'第一阶段|征集阶段|入围阶段|此前|当时', clause):
                continue  # Historical first-stage text is not a denial of this order.
            return True
    if actual_order_title:
        return False
    return (any(_FIRST_STAGE_TITLE.search(title) for title in titles)
            or any('框架协议' in title for title in titles) and bool(_INTENT_BODY.search('\n'.join(chunks))))


def exclude_intention_trade(candidates, index, bundle):
    if not intention_only(index):
        return False
    note = issue(bundle.dataset_id, index.announcement_id, 'intention_only_excluded',
        candidate_ids=[c.candidate_id for c in candidates if c.field_kind in
            {'winning_supplier', 'bidder', 'project_amount', 'product_reference'}],
        details={'classification': 'intention_only',
                 'note': '仅入围/采购意向；不作为实际成交、交易金额或共同竞标事实。原文和候选保留。'})
    if not any(existing.issue_id == note.issue_id for existing in bundle.issues):
        bundle.issues.append(note)
    return True
