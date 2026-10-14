"""Read-only, version-pinned queries over validated relationship facts.

Source-name lookup is not legal-entity resolution. Occurrence counts remain
separate from eligible transaction and amount metrics, including when empty.
"""
from collections import defaultdict
from decimal import Decimal, localcontext


class RelationshipQueries:
    BASIS = 'verified_source_occurrences_with_separate_eligible_metrics'

    def __init__(self, dataset):
        self.dataset_id = dataset['dataset_id']
        self.facts = {f['record_id']: f for f in dataset['facts']}
        if len(self.facts) != len(dataset['facts']):
            raise ValueError('duplicate fact record_id')
        if any(f['dataset_id'] != self.dataset_id for f in self.facts.values()):
            raise ValueError('mixed dataset versions')
        self.evidence = {e['evidence_id']: e for e in dataset.get('evidence', [])}
        self.sources = {s['source_id']: s for s in dataset.get('sources', [])}
        self.by_kind = defaultdict(list)
        for f in self.facts.values():
            if f['validation_status'] == 'verified':
                self.by_kind[f['kind']].append(f)
        self.organizations = {f['record_id']: f for f in self.by_kind['organization']}
        self.projects = {f['record_id']: f for f in self.by_kind['project']}
        self.events = {f['record_id']: f for f in self.by_kind['event']}
        self.subjects = {f['record_id']: f for f in self.by_kind['bid_subject']}

    def envelope(self, **values):
        return dict(dataset_id=self.dataset_id, metric_basis=self.BASIS,
                    name_matching='exact_source_name_not_legal_identity_merge', **values)

    @staticmethod
    def options(scope_level, limit):
        if scope_level not in ('project', 'event'):
            raise ValueError('scope_level must be project or event')
        if not isinstance(limit, int) or not 1 <= limit <= 10000:
            raise ValueError('internal result limit must be between 1 and 10000')

    def ids(self, selector):
        if not isinstance(selector, str) or not selector.strip():
            raise ValueError('organization selector required')
        ids = {rid for rid, f in self.organizations.items()
               if selector == rid or selector == f['fields']['raw_name']}
        if not ids:
            raise ValueError('organization not found in verified sources')
        return ids

    def name(self, rid):
        return self.organizations[rid]['fields']['raw_name']

    def subject(self, rid):
        obj = self.subjects.get(rid)
        if not obj or not obj['fields']['member_ids'] or any(
                x not in self.organizations for x in obj['fields']['member_ids']):
            return None
        names = sorted(self.name(x) for x in obj['fields']['member_ids'])
        return dict(name=' + '.join(names), subject_kind=obj['fields']['subject_kind'],
                    members=names, subject_id=rid)

    def member(self, fact, ids):
        subject = self.subjects.get(fact['fields'].get('subject_id'))
        return bool(subject and ids.intersection(subject['fields']['member_ids']))

    def project_id(self, fact):
        fields = fact['fields']
        if fact['kind'] == 'project':
            return fact['record_id']
        sid = fields.get('scope_id')
        if sid in self.projects:
            return sid
        return self.events.get(sid, {}).get('fields', {}).get('project_id')

    def scope_key(self, fact, level):
        if level == 'project':
            return self.project_id(fact)
        sid = fact['fields'].get('scope_id')
        ev = self.events.get(sid)
        return sid if ev and ev['fields']['scope_status'] == 'resolved' else None

    def announcements(self, facts):
        return sorted({self.sources[self.evidence[eid]['source_id']]['announcement_id']
                       for f in facts for eid in f['evidence_ids']
                       if eid in self.evidence and self.evidence[eid]['source_id'] in self.sources})

    def money(self, facts):
        """Only eligible, attributed CNY totals with explicit non-overlap."""
        values = {f['record_id']: f for f in facts
                  if f['validation_status'] == 'verified'
                  and f['aggregation_eligibility'] == 'eligible'
                  and f['fields']['amount_kind'] in ('award_total', 'project_total', 'product_line_total')
                  and f['fields']['currency'] == 'CNY'
                  and f['fields']['decimal_value'] is not None}
        if not values:
            return None
        if any(f['fields']['coverage_status'] not in ('isolated', 'explicit_parent', 'explicit_child')
               or f['fields']['overlaps_amount_ids'] for f in values.values()):
            return None
        covered = {cid for f in values.values() for cid in f['fields']['covers_amount_ids']}
        if not covered <= values.keys():
            return None
        def cycle(rid, path):
            return rid in path or any(cycle(c, path | {rid}) for c in values[rid]['fields']['covers_amount_ids'])
        if any(cycle(rid, set()) for rid in values):
            return None
        with localcontext() as ctx:
            ctx.prec = 80
            return format(sum((Decimal(f['fields']['decimal_value']) for rid, f in values.items()
                               if rid not in covered), Decimal(0)), 'f')

    def metrics(self, facts, level, amounts=()):
        projects = {self.scope_key(f, level) for f in facts} - {None}
        eligible = [f for f in facts if f['aggregation_eligibility'] == 'eligible'
                    and self.scope_key(f, level) is not None
                    and self.projects.get(self.project_id(f), {}).get('identity_status') == 'resolved']
        return dict(announcement_count=len(self.announcements(facts)),
                    observed_scope_count=len(projects),
                    transaction_count=len({self.scope_key(f, level) for f in eligible}) if eligible else None,
                    ineligible_record_count=len(facts)-len(eligible),
                    amount_cny=self.money(amounts),
                    record_ids=sorted(f['record_id'] for f in facts),
                    evidence_ids=sorted({eid for f in facts for eid in f['evidence_ids']}),
                    announcement_ids=self.announcements(facts))

    def buyer_projects(self, selector):
        buyers = self.ids(selector)
        return {rid for rid, p in self.projects.items() if p['fields']['buyer_organization_id'] in buyers}

    def award_amounts(self, awards):
        return [self.facts[x] for f in awards for x in f['fields']['amount_ids']
                if x in self.facts and self.facts[x]['kind'] == 'amount'
                and self.facts[x]['fields']['owner_subject_id'] == f['fields']['subject_id']
                and self.facts[x]['fields']['scope_id'] == f['fields']['scope_id']]

    def buyer_suppliers(self, selector, scope_level='project', limit=200):
        self.options(scope_level, limit)
        projects = self.buyer_projects(selector)
        groups = defaultdict(list)
        for f in self.by_kind['award']:
            s = self.subject(f['fields']['subject_id'])
            if s and self.project_id(f) in projects:
                groups[('award_supplier', s['name'], s['subject_kind'])].append(f)
        for f in self.by_kind['supply']:
            oid = f['fields']['provider_organization_id']
            if self.project_id(f) in projects and f['fields']['provider_kind'] == 'product_supplier' and oid in self.organizations:
                groups[('product_supplier', self.name(oid), 'organization')].append(f)
        rows = []
        for (role, name, kind), fs in groups.items():
            # Product amounts need a direct provider attribution: never copy winner totals.
            amounts = self.award_amounts(fs) if role == 'award_supplier' else []
            rows.append(dict(name=name, role=role, subject_kind=kind,
                             **self.metrics(fs, scope_level, amounts)))
        rows.sort(key=lambda r: (-r['announcement_count'], r['name'], r['role']))
        return self.envelope(scope_level=scope_level, total=len(rows), items=rows[:limit])

    def bidding(self, projects):
        return [f for f in self.by_kind['participation'] if self.project_id(f) in projects
                and f['fields']['participation_kind'] == 'bid_participation'
                and self.subject(f['fields']['subject_id'])]

    def participant_rows(self, parts, level):
        groups = defaultdict(list)
        for f in parts:
            s = self.subject(f['fields']['subject_id'])
            groups[(s['name'], s['subject_kind'])].append(f)
        rows = []
        for (name, kind), fs in groups.items():
            scopes = {(f['fields']['scope_id'], f['fields']['subject_id']) for f in fs}
            awards = [a for a in self.by_kind['award']
                      if (a['fields']['scope_id'], a['fields']['subject_id']) in scopes]
            rows.append(dict(name=name, subject_kind=kind,
                             **self.metrics(fs, level, self.award_amounts(awards))))
        return sorted(rows, key=lambda r: (-r['announcement_count'], r['name']))

    def buyer_bidders(self, selector, scope_level='project', limit=200):
        self.options(scope_level, limit)
        parts = self.bidding(self.buyer_projects(selector))
        ranks = self.participant_rows(parts, scope_level)
        by_scope = defaultdict(list)
        for f in parts:
            key = self.scope_key(f, scope_level)
            if key:
                by_scope[key].append(f)
        combinations = defaultdict(list)
        for key, fs in by_scope.items():
            names = tuple(sorted({self.subject(f['fields']['subject_id'])['name'] for f in fs}))
            if len(names) >= 2:
                combinations[names].append((key, fs))
        combo_rows = []
        for names, groups in combinations.items():
            fs = [f for _, group in groups for f in group]
            # A roster must prove completeness at this exact scope, not merely list names.
            complete = all(any(r['fields']['scope_id'] == key and r['fields']['scope_level'] == scope_level
                               and r['fields']['roster_kind'] == 'all_bidders'
                               and r['fields']['disclosure_status'] == 'disclosed_complete'
                               and r['fields']['completeness_evidence_ids']
                               and set(r['fields']['validated_subject_ids']) == {f['fields']['subject_id'] for f in group}
                               for r in self.by_kind['roster']) for key, group in groups)
            combo_rows.append(dict(names=list(names), roster_complete=complete,
                                   combination_basis='known_disclosed_bid_subject_set',
                                   **self.metrics(fs, scope_level)))
        combo_rows.sort(key=lambda r: (-r['announcement_count'], r['names']))
        return self.envelope(scope_level=scope_level, top5=ranks[:5],
                             total=len(ranks), items=ranks[:limit], combinations_total=len(combo_rows),combinations=combo_rows[:limit])

    def winner_ids(self, selector):
        ids = self.ids(selector)
        if not any(self.member(f, ids) for f in self.by_kind['award']):
            raise ValueError('selector has no verified award role')
        return ids

    def supplier_peers(self, selector, scope_level='project', limit=200):
        self.options(scope_level, limit)
        ids = self.winner_ids(selector)
        all_parts = self.bidding(set(self.projects))
        keys = {self.scope_key(f, scope_level) for f in all_parts if self.member(f, ids)} - {None}
        parts = [f for f in all_parts if self.scope_key(f, scope_level) in keys]
        rows = self.participant_rows(parts, scope_level)
        peers = self.participant_rows([f for f in parts if not self.member(f, ids)], scope_level)
        return self.envelope(scope_level=scope_level, top5=peers[:5], participants=rows[:limit],
                             participants_total=len(rows),
                             nonwinning_participants=self.participant_rows(
                                 [f for f in parts if f['fields']['result'] == 'lost'], scope_level)[:limit],
                             unknown_result_count=len([f for f in parts if f['fields']['result'] == 'unknown']))

    def multi(self, selectors):
        if not isinstance(selectors, list) or not 2 <= len(selectors) <= 10 or len(set(selectors)) != len(selectors):
            raise ValueError('provide 2 to 10 distinct winner selectors')
        groups = [self.winner_ids(x) for x in selectors]
        if any(a & b for n, a in enumerate(groups) for b in groups[n+1:]):
            raise ValueError('selectors resolve to overlapping source organizations')
        return groups

    def common_buyers(self, selectors, scope_level='project', limit=200):
        self.options(scope_level, limit)
        actor_ids = self.multi(selectors)
        matched = [defaultdict(list) for _ in selectors]
        for f in self.by_kind['award']:
            p = self.projects.get(self.project_id(f))
            buyer = p['fields']['buyer_organization_id'] if p else None
            if buyer not in self.organizations:
                continue
            for n, ids in enumerate(actor_ids):
                if self.member(f, ids):
                    matched[n][self.name(buyer)].append(f)
        common = set(matched[0]).intersection(*(set(x) for x in matched[1:]))
        rows = []
        for name in common:
            by_supplier = {}
            for n, label in enumerate(selectors):
                fs = matched[n][name]
                indiv = [f for f in fs if self.subjects[f['fields']['subject_id']]['fields']['subject_kind'] == 'individual']
                by_supplier[label] = self.metrics(fs, scope_level, self.award_amounts(indiv))
                by_supplier[label]['amount_basis'] = 'individual_awards_only_no_consortium_allocation'
            rows.append(dict(name=name, by_supplier=by_supplier))
        rows.sort(key=lambda r: r['name'])
        return self.envelope(scope_level=scope_level, total=len(rows), items=rows[:limit])

    def common_projects(self, selectors, scope_level='project', limit=200):
        self.options(scope_level, limit)
        groups = self.multi(selectors)
        parts = self.bidding(set(self.projects))
        scopes = [{self.scope_key(f, scope_level) for f in parts if self.member(f, ids)} - {None} for ids in groups]
        common = scopes[0].intersection(*scopes[1:])
        rows = []
        for key in sorted(common):
            fs = [f for f in parts if self.scope_key(f, scope_level) == key]
            pid = self.project_id(fs[0])
            results = {}
            for label, ids in zip(selectors, groups):
                values = {f['fields']['result'] for f in fs if self.member(f, ids)}
                results[label] = next(iter(values)) if len(values) == 1 else 'mixed'
            amounts = [f for f in self.by_kind['amount'] if f['fields']['amount_kind'] == 'project_total'
                       and f['fields']['scope_id'] == pid]
            rows.append(dict(project_id=pid, scope_id=key, project_name=self.projects[pid]['fields']['raw_name'],
                             results=results, project_amount_cny=self.money(amounts),
                             **self.metrics(fs, scope_level)))
        return self.envelope(scope_level=scope_level, project_count=len({r['project_id'] for r in rows}),
                             total=len(rows), items=rows[:limit])

    def choices(self, role, term='', limit=50):
        if role not in ('buyer', 'winner') or len(term) > 120 or not 1 <= limit <= 100:
            raise ValueError('invalid organization choice filter')
        ids = {p['fields']['buyer_organization_id'] for p in self.projects.values()} if role == 'buyer' else {
            oid for f in self.by_kind['award'] for oid in self.subjects.get(f['fields']['subject_id'], {}).get('fields', {}).get('member_ids', [])}
        names = sorted({self.name(oid) for oid in ids if oid in self.organizations and term in self.name(oid)})
        return self.envelope(total=len(names), items=[dict(value=n, label=n) for n in names[:limit]])
