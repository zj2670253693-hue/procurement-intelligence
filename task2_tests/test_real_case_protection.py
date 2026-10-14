from pathlib import Path
import os
import pytest
from task2_relationship_analysis.inputs import snapshot_inputs, load_candidates
from task2_relationship_analysis.serialization import implementation_fingerprint
from task2_relationship_analysis.source_index import index_html
from task2_relationship_analysis.identities import resolve_identities
from task2_relationship_analysis.relations import verify_relations
from task2_relationship_analysis.rosters import verify_rosters
from task2_relationship_analysis.amounts import verify_amounts
from task2_relationship_analysis.builder import PINNED_LEGACY, PINNED_V11

pytestmark = pytest.mark.private_data
HTML = Path(os.environ['TASK2_PRIVATE_HTML_ROOT']) if os.getenv('TASK2_PRIVATE_HTML_ROOT') else None
LEGACY = Path(os.environ['TASK2_PRIVATE_LEGACY_RAW']) if os.getenv('TASK2_PRIVATE_LEGACY_RAW') else None
V11 = Path(os.environ['TASK2_PRIVATE_V11_RAW']) if os.getenv('TASK2_PRIVATE_V11_RAW') else None

def test_actual_fee_case_never_becomes_award_total():
    if not all((HTML, LEGACY, V11)):
        pytest.skip('Private source test requires TASK2_PRIVATE_HTML_ROOT, TASK2_PRIVATE_LEGACY_RAW and TASK2_PRIVATE_V11_RAW')
    aid='t20260203_26146674'
    raw=(HTML/(aid+'.html')).read_bytes()
    # Independent source expectation, not generated with a production locator.
    text=raw.decode('utf8')
    assert '代理服务收费金额' in text and '710155' in text
    assert all(x in text for x in ['报价：6.5（%）','报价：15（%）','报价：30.3（%）'])
    snapshot=snapshot_inputs(HTML,LEGACY,V11,{'real_protection':'fixed'})
    assert snapshot.input_manifest['legacy']['sha256']==PINNED_LEGACY
    assert snapshot.input_manifest['v11']['sha256']==PINNED_V11
    cs=[c for c in load_candidates(snapshot)[0] if c.announcement_id==aid]
    ix=index_html(aid,raw,snapshot.dataset_id)
    b=resolve_identities(cs,ix,snapshot.dataset_id)
    for fn in (verify_relations,verify_rosters,verify_amounts): b=fn(cs,ix,b)
    amounts=[f for f in b.facts if f.kind=='amount']
    assert any(f.fields['amount_kind']=='agency_fee' and f.fields['decimal_value']=='710155' for f in amounts)
    assert not [f for f in amounts if f.fields['amount_kind']=='award_total' and f.aggregation_eligibility=='eligible']
    assert not [f for f in amounts if f.fields['amount_kind']=='award_total' and f.fields['decimal_value']=='710155']
