import importlib
from task2_tests.test_identities import BASE, table, candidates
from task2_tests.test_relations import relations
from task2_relationship_analysis.source_index import index_html

def review(title='全部投标人名单', names=('甲公司','乙公司')):
    return f'<p>{title} 合同包1</p><table><tr><th>供应商</th><th>资格性审查</th><th>综合得分</th></tr>' + ''.join(f'<tr><td>{n}</td><td>通过</td><td>90</td></tr>' for n in names) + '</table>'

def roster(body, cs):
    b = relations(body, cs)
    return importlib.import_module('task2_relationship_analysis.rosters').verify_rosters(cs, index_html('t20260101_1', body.encode(), 'd'), b)

def test_empty_topn_qualified_complete_and_nonroster_all_word():
    cs = candidates('甲公司', bidders=['甲公司','乙公司'])
    for title, expected in [('全部投标人名单','disclosed_complete'), ('前三名候选供应商','disclosed_partial'), ('仅合格供应商','disclosed_partial'), ('全部投标人通过初审','disclosed_partial')]:
        b = roster(BASE + table('甲公司') + review(title), cs)
        assert expected in [f.fields['disclosure_status'] for f in b.facts if f.kind == 'roster']
    b = roster(BASE + table('甲公司'), candidates('甲公司'))
    assert all(f.fields['disclosure_status'] == 'not_disclosed' for f in b.facts if f.kind == 'roster')

def test_candidate_missing_source_member_is_partial():
    b = roster(BASE + table('甲公司') + review(), candidates('甲公司', bidders=['甲公司']))
    r = next(f for f in b.facts if f.kind == 'roster' and f.fields['declared_names'])
    assert r.fields['declared_names'] == ['甲公司','乙公司']
    assert r.fields['disclosure_status'] == 'disclosed_partial'
    assert 'roster_member_uncovered' in {i.reason_code for i in b.issues}

def test_lost_requires_same_scope_complete_result():
    cs = candidates('甲公司', bidders=['甲公司','乙公司'])
    for marker, expected in [('', 'unknown'), ('<p>合同包1 完整中标结果</p>', 'lost')]:
        b = roster(BASE + table('甲公司') + marker + review(), cs)
        loser = [f for f in b.facts if f.kind == 'participation' and f.fields['participation_kind']=='bid_participation' and f.fields['result'] != 'won']
        assert len(loser) == 1 and loser[0].fields['result'] == expected
