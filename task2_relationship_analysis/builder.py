from pathlib import Path
from collections import defaultdict
from .contracts import Bundle, Source, InputContractError, InputChangedError
from .serialization import implementation_fingerprint, make_record_id
from .inputs import snapshot_inputs, load_candidates, assert_inputs_unchanged, safe_path
from .source_index import index_html
from .identities import resolve_identities
from .relations import verify_relations
from .rosters import verify_rosters
from .amounts import verify_amounts
from .validation import validate_bundle, require_valid
from .publication import write_artifacts, output_path
from .offline import OfflineBoundary

PINNED_LEGACY='ba22af4644b4d2d06592536dce0b28a63228599cd0343ffa438aff308057b4ef'
PINNED_V11='1878f9237fe3519dd3d1ce38c0835ca7e85dabc28d433be10e30725e66761c8e'

def build_dataset(html_root,legacy_path,v11_path,output_root,expected_count=None,*,input_profile=None):
    with OfflineBoundary():
        package=Path(__file__).parent
        impl=implementation_fingerprint(package)
        snapshot=snapshot_inputs(html_root,legacy_path,v11_path,impl,input_profile=input_profile)
        output_path(output_root,snapshot)
        if expected_count is not None and len(snapshot.html_entries)!=expected_count: raise InputContractError('announcement count does not match')
        if input_profile is None and expected_count==1038 and (snapshot.input_manifest['legacy']['sha256']!=PINNED_LEGACY or snapshot.input_manifest['v11']['sha256']!=PINNED_V11): raise InputContractError('1038 acceptance input hashes do not match')
        cs,issues=load_candidates(snapshot)
        by_aid=defaultdict(list)
        for c in cs: by_aid[c.announcement_id].append(c)
        all_bundle=Bundle(snapshot.dataset_id,candidates=cs,issues=issues)
        maps={key:{} for key in ('sources','facts','evidence','issues')}
        for i in issues: maps['issues'][i.issue_id]=i
        for aid,path in sorted(snapshot.html_entries.items()):
            if path is None:
                sid=make_record_id(snapshot.dataset_id,'source',(aid,'missing'))
                maps['sources'][sid]=Source(snapshot.dataset_id,sid,aid,None,None,'source_missing')
                continue
            raw=safe_path(path).read_bytes()
            index=index_html(aid,raw,snapshot.dataset_id)
            b=resolve_identities(by_aid[aid],index,snapshot.dataset_id)
            for function in (verify_relations,verify_rosters,verify_amounts): b=function(by_aid[aid],index,b)
            for key,id_key in [('sources','source_id'),('facts','record_id'),('evidence','evidence_id'),('issues','issue_id')]:
                for value in getattr(b,key):
                    rid=getattr(value,id_key)
                    old=maps[key].get(rid)
                    if old and key=='facts':
                        old.evidence_ids=sorted(set(old.evidence_ids+value.evidence_ids))
                        old.candidate_ids=sorted(set(old.candidate_ids+value.candidate_ids))
                        if old.kind=='organization' and old.fields['identity_basis']['kind']=='credit_code':
                            old.fields['raw_name']=min(old.fields['raw_name'],value.fields['raw_name'])
                            old.fields['alias_evidence_ids']=sorted(set(old.fields['alias_evidence_ids']+value.fields['alias_evidence_ids']))
                        elif old.fields!=value.fields:
                            old.validation_status='conflict';old.aggregation_eligibility='not_applicable'
                    elif old and key=='evidence':
                        old.fact_ids=sorted(set(old.fact_ids+value.fact_ids));old.candidate_ids=sorted(set(old.candidate_ids+value.candidate_ids))
                    else: maps[key][rid]=value
            del b,index,raw
        for key in maps: setattr(all_bundle,key,list(maps[key].values()))
        # End checks precede validation so changed-source runs abort as input changes.
        assert_inputs_unchanged(snapshot)
        if implementation_fingerprint(package)!=impl: raise InputChangedError('implementation changed during build')
        report=validate_bundle(all_bundle,snapshot);require_valid(report)
        assert_inputs_unchanged(snapshot)
        if implementation_fingerprint(package)!=impl: raise InputChangedError('implementation changed during validation')
        return write_artifacts(all_bundle,report,snapshot,output_root)

