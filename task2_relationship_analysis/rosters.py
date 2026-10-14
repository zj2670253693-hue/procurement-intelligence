from copy import deepcopy
from .identities import table_records, bidding_record, name_cell, event_for_record, subject_for_cell, add_fact, scope_level, NAME_HEADERS
from .evidence import region_evidence, normalized
from .inputs import issue

def verify_rosters(candidates, index, relations):
    b = deepcopy(relations)
    by_event = {}
    parsed={}
    for table, record in table_records(index):
        parsed.setdefault(table.table,[]).append(record)
    def category(text):
        if '候选' in text or '前三名' in text or 'TOP' in text.upper(): return 'topn_candidates'
        if '仅合格' in text or '合格供应商名单' in text: return 'qualified_only'
        if '全部投标人名单' in text or '完整投标名单' in text: return 'all_bidders'
        return 'unknown'
    for table in index.tables:
        previous=[s for s in index.sections if s.end<=table.start]
        near=previous[-1].text if previous else ''
        context=table.heading+' | '+near
        headers=[normalized(c.text) for c in table.rows[0]] if table.rows else []
        kind=category(context)
        relevant=kind!='unknown' or any(h in NAME_HEADERS for h in headers) and any(x in h for h in headers for x in ('资格性','符合性','综合得分','评审得分','推荐排名'))
        if not relevant: continue
        rows=parsed.get(table.table,[])
        unsupported=table.malformed or any(c.rowspan!=1 or c.colspan!=1 for row in table.rows for c in row) or any(len(row)!=len(headers) and any(c.text.strip() for c in row) for row in table.rows[1:])
        if not rows:
            event=event_for_record(b,table)
            if event: by_event.setdefault((event.record_id,kind),[]).append((table,None,context,unsupported))
        for record in rows:
            event=event_for_record(b,table,record)
            if event: by_event.setdefault((event.record_id,kind),[]).append((table,record,context,unsupported))
    for event in [f for f in b.facts if f.kind == "event"]:
      kinds=sorted(k for eid,k in by_event if eid==event.record_id) or ['unknown']
      for kind in kinds:
        rows = by_event.get((event.record_id,kind), [])
        names, subjects, proofs, completeness = [], [], [], []
        complete = bool(rows) and event.fields["scope_status"] == "resolved"
        failed=any(unsupported for _,_,_,unsupported in rows)
        for table, record,context,unsupported in rows:
            if record is None or unsupported:
                complete=False
                continue
            cell = name_cell(record)
            if not cell or not cell.text.strip():
                complete = False
                continue
            raw = cell.text.strip()
            names.append(raw)
            proof = region_evidence(None, index, cell)
            proofs.append(proof)
            subject = subject_for_cell(b, cell)
            if subject:
                subjects.append(subject.record_id)
            else:
                complete = False
                b.issues.append(issue(b.dataset_id, index.announcement_id, "roster_member_uncovered", locator=proof.locator))
            heading = normalized(context)
            explicit = any(x in heading for x in ("全部投标人名单", "完整投标名单")) and not any(x in heading for x in ("候选", "前", "合格", "仅"))
            # Every table supplying this roster needs a completeness declaration.
            complete &= explicit
            if explicit:
                completeness += [region_evidence(None, index, s) for s in index.sections if s.text.strip() in context and s.end<=table.start and ("全部投标人名单" in s.text or "完整投标名单" in s.text)]
            if any(len(row) != len(table.rows[0]) and any(c.text.strip() for c in row) for row in table.rows[1:]):
                complete = False
        status = 'parse_failed' if failed else "not_disclosed" if not names else "disclosed_complete" if complete and completeness else "disclosed_partial"
        roster = add_fact(b, "roster", (event.record_id, kind), {"scope_id": event.record_id, "scope_level": scope_level(event), "roster_kind": kind, "declared_names": list(dict.fromkeys(names)), "validated_subject_ids": sorted(set(subjects)), "disclosure_status": status, "completeness_evidence_ids": sorted(set(p.evidence_id for p in completeness))}, proofs + completeness, state="verified" if names and not failed else "unresolved", eligibility="not_applicable")
        lot = event.fields["lot_raw"]
        complete_result = [s for s in index.sections if lot and lot in s.text and "完整中标结果" in s.text]
        winners = {f.fields["subject_id"] for f in b.facts if f.kind == "award" and f.fields["scope_id"] == event.record_id and f.validation_status == "verified"}
        for participation in b.facts:
            if participation.kind != "participation" or participation.validation_status!='verified' or participation.fields["scope_id"] != event.record_id or participation.fields["participation_kind"] != "bid_participation":
                continue
            if kind=='all_bidders' or participation.fields['roster_id'] is None:
                participation.fields["roster_id"] = roster.record_id
            if participation.fields["subject_id"] in winners:
                participation.fields["result"] = "won"
            elif status == "disclosed_complete" and complete_result and winners and participation.fields["qualification_raw"] in ("通过", "合格"):
                participation.fields["result"] = "lost"
                add_fact(b, "participation", participation.natural_key, participation.fields, [region_evidence(None, index, s) for s in complete_result] + completeness)
        if status != "disclosed_complete":
            b.issues.append(issue(b.dataset_id, index.announcement_id, 'parse_failed' if failed else "participants_not_disclosed" if not names else "roster_partial", fact_ids=[roster.record_id]))
    return b
