import unicodedata
from .contracts import Candidate, Evidence, Locator, PhysicalCell, LOCATOR_VERSION
from .serialization import make_record_id
from .inputs import issue

def normalized(value):
    return "".join(unicodedata.normalize("NFKC", value).split())

def _mapping(region):
    chars, positions = [], []
    for char, source in zip(region.text, region.positions):
        for output in unicodedata.normalize("NFKC", char):
            if not output.isspace():
                chars.append(output)
                positions.append(source)
    result = "".join(chars)
    # Composition across characters cannot be guessed from a per-character map.
    return (result, positions) if result == normalized(region.text) else ("", [])

def region_evidence(candidate, index, region, start=None, end=None):
    start = region.positions[0][0] if start is None else start
    end = region.positions[-1][1] if end is None else end
    loc = Locator(start, end, region.title)
    context = region.title
    if isinstance(region, PhysicalCell):
        loc.table, loc.row, loc.cell = region.table, region.row, region.cell
        loc.parent_physical_cell = (region.table, region.row, region.cell)
        table = index.tables[region.table]
        if table.rows:
            context += " | " + " | ".join(c.text for c in table.rows[0])
    cid = candidate.candidate_id if candidate else ""
    eid = make_record_id(index.dataset_id, "evidence", (index.source_id, str(start), str(end), cid))
    return Evidence(index.dataset_id, eid, index.source_id, loc, index.decoded_html[start:end], context, [cid] if cid else [], [])

def locate_candidate(candidate: Candidate, index):
    value = candidate.raw_value
    if isinstance(value, dict) and candidate.field_kind == "product_reference":
        value = value.get("brand")
    if not isinstance(value, str) or not normalized(value):
        return [], []
    target = normalized(value)
    regions = list(index.sections)
    regions.extend(c for table in index.tables if not table.malformed for row in table.rows for c in row)
    proofs = []
    for region in regions:
        text, positions = _mapping(region)
        at = 0
        while (found := text.find(target, at)) >= 0:
            start, end = positions[found][0], positions[found + len(target) - 1][1]
            proofs.append(region_evidence(candidate, index, region, start, end))
            at = found + len(target)
    problems = [] if proofs else [issue(index.dataset_id, index.announcement_id, "value_not_located", [candidate.candidate_id])]
    return proofs, problems

def verify_locator(evidence, index):
    loc = evidence.locator
    if evidence.dataset_id != index.dataset_id or evidence.source_id != index.source_id or loc.version != LOCATOR_VERSION or not (0 <= loc.start < loc.end <= len(index.decoded_html)):
        return False
    if index.decoded_html[loc.start:loc.end] != evidence.raw_quote:
        return False
    if loc.table is not None:
        try:
            table = index.tables[loc.table]
            cell = table.rows[loc.row][loc.cell]
            return not table.malformed and cell.start <= loc.start < loc.end <= cell.end and loc.parent_physical_cell == (loc.table, loc.row, loc.cell)
        except (IndexError, TypeError):
            return False
    return any(s.start <= loc.start < loc.end <= s.end for s in index.sections)
