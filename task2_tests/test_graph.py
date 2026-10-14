from task2_tests.test_queries import fixture, query


def test_graph_links_are_proved_and_contain_consortium_members_not_competition():
    from task2_query_service.graph import relationship_graph
    data=fixture();next(f for f in data['facts'] if f['record_id']=='bsa')['fields'].update(subject_kind='consortium',member_ids=['a','c'])
    graph=relationship_graph(query(data),['a1','part1b','supply1'])
    ids={n['id'] for n in graph['nodes']}
    assert all(e['from'] in ids and e['to'] in ids and e['fact_id'] and e['evidence_ids'] for e in graph['edges'])
    assert any(e['kind']=='consortium_member' and e['to']=='c' for e in graph['edges'])
    assert not any(e['kind']=='competition' for e in graph['edges'])
    assert any(e['kind']=='product_supplier' and e['to']=='manufacturer' for e in graph['edges'])
    assert graph['dataset_id']=='d'


def test_graph_limits_never_leave_dangling_edges_or_claim_full_graph():
    from task2_query_service.graph import relationship_graph
    graph=relationship_graph(query(),['a1','a2','part1b','part2f'],max_nodes=3,max_edges=2)
    ids={n['id'] for n in graph['nodes']}
    assert len(ids)<=3 and len(graph['edges'])<=2 and graph['truncated']
    assert all(e['from'] in ids and e['to'] in ids for e in graph['edges'])


def test_unverified_or_unknown_graph_seed_cannot_create_edges():
    from task2_query_service.graph import relationship_graph
    data=fixture();next(f for f in data['facts'] if f['record_id']=='a1')['validation_status']='conflict'
    assert relationship_graph(query(data),['a1','missing'])['edges']==[]
