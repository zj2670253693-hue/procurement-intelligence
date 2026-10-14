from pathlib import Path
from dataclasses import asdict
import uuid
from .contracts import Bundle, Source, Candidate, Fact, Evidence, Issue, Locator, BuildResult, ArtifactConflictError, SCHEMA_VERSION, RULE_VERSION
from .serialization import canonical_bytes, sha256
from .inputs import safe_path, strict_json, snapshot_inputs, assert_inputs_unchanged
from .validation import validate_bundle, require_valid
from .offline import OfflineBoundary

FILES=('candidates.jsonl','facts.jsonl','evidence.jsonl','issues.jsonl','coverage.json')

def _write_file(path,raw):
    with path.open('xb') as stream:
        stream.write(raw);stream.flush()

def output_path(root,snapshot):
    root=safe_path(root,must_exist=False)
    receipt_root=safe_path(root.parent/'run_receipts',must_exist=False)
    for write_root in (root,receipt_root):
        for input_path in (snapshot.html_root,snapshot.legacy_path.parent,snapshot.v11_path.parent):
            if write_root==input_path or write_root in input_path.parents or input_path in write_root.parents:
                from .contracts import InputContractError
                raise InputContractError('output/receipts overlap pinned input directory')
    return root

def write_artifacts(bundle,report,snapshot,output_root):
    require_valid(report)
    root=output_path(output_root,snapshot)
    root.mkdir(parents=True,exist_ok=True)
    root=safe_path(root)
    records={'candidates.jsonl':(bundle.candidates,'candidate_id'),'facts.jsonl':(bundle.facts,'record_id'),'evidence.jsonl':(bundle.evidence,'evidence_id'),'issues.jsonl':(bundle.issues,'issue_id')}
    payload={name:b''.join(canonical_bytes(v) for v in sorted(rows,key=lambda x:getattr(x,key))) for name,(rows,key) in records.items()}
    payload['coverage.json']=canonical_bytes(report.coverage)
    manifest={'dataset_id':bundle.dataset_id,'schema_version':SCHEMA_VERSION,'rule_version':RULE_VERSION,'status':'completed','input_manifest':snapshot.input_manifest,'implementation':snapshot.implementation,'output_hashes':{name:sha256(raw) for name,raw in payload.items()},'sources':sorted(bundle.sources,key=lambda s:s.source_id),'counts':{name:len(rows) for name,(rows,key) in records.items()},'new_model_calls':0,'legacy_producer':'legacy_model_unknown','processing_mode':'one_html_at_a_time','runtime_note':'Clocks and input paths are separate local receipts; no DB/Task1/model modules loaded.'}
    payload['manifest.json']=canonical_bytes(manifest)
    profile=snapshot.input_manifest.get('input_profile')
    if profile:
        manifest['legacy_producer']=profile['producer']
        manifest['input_profile']=profile
        payload['manifest.json']=canonical_bytes(manifest)
    target=root/bundle.dataset_id
    if target.exists():
        safe_path(target)
        if {p.name for p in target.iterdir()}!=set(payload): raise ArtifactConflictError('existing version file catalog differs')
        for name,raw in payload.items():
            if safe_path(target/name).read_bytes()!=raw: raise ArtifactConflictError('existing version bytes differ: '+name)
        status='reused'
    else:
        pending=root/('.pending-'+uuid.uuid4().hex)
        pending.mkdir()
        for name in FILES: _write_file(pending/name,payload[name])
        _write_file(pending/'manifest.json',payload['manifest.json'])
        assert_inputs_unchanged(snapshot)
        if target.exists(): raise ArtifactConflictError('concurrent version appeared')
        pending.rename(target)
        status='completed'
    receipts=root.parent/'run_receipts'
    receipts=safe_path(receipts,must_exist=False);receipts.mkdir(exist_ok=True)
    receipt=receipts/(bundle.dataset_id+'.inputs.json')
    if not receipt.exists():
        _write_file(receipt,canonical_bytes({'dataset_id':bundle.dataset_id,'html_root':str(snapshot.html_root),'legacy_path':str(snapshot.legacy_path),'v11_path':str(snapshot.v11_path)}))
    else: safe_path(receipt)
    return BuildResult(bundle.dataset_id,status,str(target),strict_json(payload['manifest.json']))

def _read_bundle(path,manifest):
    def lines(name):
        return [strict_json(line) for line in safe_path(path/name).read_bytes().splitlines() if line]
    b=Bundle(manifest['dataset_id'],sources=[Source(**x) for x in manifest['sources']])
    b.candidates=[Candidate(**x) for x in lines('candidates.jsonl')]
    for value in lines('facts.jsonl'):
        value['natural_key']=tuple(value['natural_key']);b.facts.append(Fact(**value))
    for value in lines('evidence.jsonl'):
        loc=value['locator']
        if loc['parent_physical_cell'] is not None: loc['parent_physical_cell']=tuple(loc['parent_physical_cell'])
        value['locator']=Locator(**loc);b.evidence.append(Evidence(**value))
    for value in lines('issues.jsonl'):
        loc=value['locator']
        if loc:
            if loc['parent_physical_cell'] is not None: loc['parent_physical_cell']=tuple(loc['parent_physical_cell'])
            value['locator']=Locator(**loc)
        b.issues.append(Issue(**value))
    return b

def verify_artifacts(dataset_dir,*,input_paths=None):
    with OfflineBoundary():
        path=safe_path(dataset_dir)
        if {p.name for p in path.iterdir()}!=set(FILES)|{'manifest.json'}: raise ArtifactConflictError('published file catalog differs')
        manifest=strict_json(safe_path(path/'manifest.json').read_bytes())
        if manifest['status']!='completed' or path.name!=manifest['dataset_id'] or manifest['new_model_calls']!=0: raise ArtifactConflictError('invalid completed version')
        if set(manifest['output_hashes'])!=set(FILES): raise ArtifactConflictError('hash catalog differs')
        for name,digest in manifest['output_hashes'].items():
            if sha256(safe_path(path/name).read_bytes())!=digest: raise ArtifactConflictError('artifact hash mismatch: '+name)
        receipt=strict_json(safe_path(path.parent.parent/'run_receipts'/(manifest['dataset_id']+'.inputs.json')).read_bytes()) if input_paths is None else input_paths
        snapshot=snapshot_inputs(Path(receipt['html_root']),Path(receipt['legacy_path']),Path(receipt['v11_path']),manifest['implementation'],input_profile=manifest['input_manifest'].get('input_profile'))
        if snapshot.dataset_id!=manifest['dataset_id'] or snapshot.input_manifest!=manifest['input_manifest']: raise ArtifactConflictError('input version no longer matches')
        bundle=_read_bundle(path,manifest)
        report=validate_bundle(bundle,snapshot);require_valid(report)
        if canonical_bytes(report.coverage)!=safe_path(path/'coverage.json').read_bytes(): raise ArtifactConflictError('coverage differs on reread')
        assert_inputs_unchanged(snapshot)
        return {'valid':True,'dataset_id':manifest['dataset_id'],'announcement_status_count':report.coverage['announcement_status_count'],'new_model_calls':0}

