"""Small proved subgraph; never manufacture pairwise competition edges."""
def relationship_graph(query, record_ids, max_nodes=100, max_edges=200):
    if not 1<=max_nodes<=100 or not 1<=max_edges<=200:
        raise ValueError('graph bounds exceeded')
    nodes={};edges={};visited=set()
    def node(rid):
        f=query.facts.get(rid)
        if not f or f['validation_status']!='verified': return False
        if f['kind']=='organization': label=f['fields']['raw_name']
        elif f['kind']=='project': label=f['fields']['raw_name']
        elif f['kind']=='event': label=f['fields']['lot_raw'] or '采购包未确定'
        elif f['kind']=='bid_subject':
            s=query.subject(rid)
            if not s: return False
            label=s['name']
        else: return False
        nodes[rid]=dict(id=rid,label=label,kind=f['kind'],identity_status=f['identity_status'],
                        evidence_ids=f['evidence_ids'],fact_id=rid)
        return True
    def edge(f,source,target,kind):
        if source and target and node(source) and node(target) and f['evidence_ids']:
            key=(f['record_id'],source,target,kind)
            edges[key]=dict(id=':'.join(key),from_=source,to=target,kind=kind,
                            fact_id=f['record_id'],evidence_ids=f['evidence_ids'],
                            aggregation_eligibility=f['aggregation_eligibility'])
            edges[key]['from']=edges[key].pop('from_')
            expand(source);expand(target)
    def expand(rid):
        if rid in visited: return
        visited.add(rid)
        f=query.facts.get(rid)
        if not f or f['validation_status']!='verified': return
        fields=f['fields'];kind=f['kind']
        if kind=='project': edge(f,rid,fields['buyer_organization_id'],'procured_by')
        elif kind=='event': edge(f,rid,fields['project_id'],'package_of')
        elif kind=='bid_subject':
            for oid in fields['member_ids']:
                edge(f,rid,oid,'consortium_member' if fields['subject_kind']=='consortium' else 'individual_subject')
        elif kind in ('award','participation'):
            edge(f,fields['scope_id'],fields['subject_id'],
                 'awarded_to' if kind=='award' else fields['participation_kind']+'_'+fields['result'])
        elif kind=='supply' and fields['provider_kind']=='product_supplier':
            edge(f,fields['scope_id'],fields['provider_organization_id'],'product_supplier')
    for rid in sorted(set(record_ids)): expand(rid)
    kept_edges=[];kept_nodes={}
    for _,e in sorted(edges.items()):
        needed={e['from'],e['to']} - kept_nodes.keys()
        if len(kept_edges)<max_edges and len(kept_nodes)+len(needed)<=max_nodes:
            kept_edges.append(e)
            for rid in needed: kept_nodes[rid]=nodes[rid]
    return dict(dataset_id=query.dataset_id,nodes=list(kept_nodes.values()),edges=kept_edges,
                total_nodes=len(nodes),total_edges=len(edges),
                truncated=len(kept_nodes)!=len(nodes) or len(kept_edges)!=len(edges),
                graph_basis='verified_facts_only_no_inferred_competition_or_collusion')
