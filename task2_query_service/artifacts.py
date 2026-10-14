"""Portable immutable loader. Private input-path receipts are not required."""
import hashlib
import json
from pathlib import Path
from task2_relationship_analysis.inputs import safe_path


def load_dataset(directory):
    path=safe_path(Path(directory))
    manifest=json.loads((path/'manifest.json').read_text(encoding='utf-8'))
    if manifest['status']!='completed' or manifest['dataset_id']!=path.name or manifest['new_model_calls']!=0:
        raise ValueError('completed pinned dataset required')
    required={'facts.jsonl','evidence.jsonl','candidates.jsonl','issues.jsonl','coverage.json'}
    if set(manifest['output_hashes'])!=required:
        raise ValueError('invalid hash catalog')
    payload={}
    for name in sorted(required):
        target=safe_path(path/name)
        if target.parent!=path:
            raise ValueError('artifact path escape')
        raw=target.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=manifest['output_hashes'][name]:
            raise ValueError('artifact hash mismatch: '+name)
        value=json.loads(raw) if name.endswith('.json') else [json.loads(line) for line in raw.splitlines() if line]
        if name in manifest['counts'] and len(value)!=manifest['counts'][name]:
            raise ValueError('artifact count mismatch')
        payload[name]=value
    for name in ('facts.jsonl','evidence.jsonl','candidates.jsonl','issues.jsonl'):
        if any(row['dataset_id']!=manifest['dataset_id'] for row in payload[name]):
            raise ValueError('cross-version artifact')
    return dict(dataset_id=manifest['dataset_id'],manifest=manifest,sources=manifest['sources'],
                facts=payload['facts.jsonl'],evidence=payload['evidence.jsonl'],
                coverage=payload['coverage.json'],coverage_text=(path/'coverage.json').read_text(encoding='utf-8'),
                candidates=payload['candidates.jsonl'],issues=payload['issues.jsonl'])
