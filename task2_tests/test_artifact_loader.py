import json
from hashlib import sha256
import pytest


def version(tmp_path):
    folder=tmp_path/'ds_test';folder.mkdir()
    contents={'facts.jsonl':b'', 'evidence.jsonl':b'', 'coverage.json':b'{}',
              'candidates.jsonl':b'', 'issues.jsonl':b''}
    for name,value in contents.items(): (folder/name).write_bytes(value)
    manifest=dict(dataset_id='ds_test',status='completed',new_model_calls=0,sources=[],
                  counts={'facts.jsonl':0,'evidence.jsonl':0,'candidates.jsonl':0,'issues.jsonl':0},
                  output_hashes={name:sha256(v).hexdigest() for name,v in contents.items()})
    (folder/'manifest.json').write_text(json.dumps(manifest),encoding='utf-8')
    return folder


def test_loader_pins_hashes_counts_and_completed_version(local_tmp):
    from task2_query_service.artifacts import load_dataset
    assert load_dataset(version(local_tmp))['dataset_id']=='ds_test'


def test_loader_rejects_tampered_rows_and_incomplete_publish(local_tmp):
    from task2_query_service.artifacts import load_dataset
    folder=version(local_tmp);(folder/'facts.jsonl').write_bytes(b'{}\n')
    with pytest.raises(ValueError,match='hash'): load_dataset(folder)
    (folder/'facts.jsonl').write_bytes(b'');m=json.loads((folder/'manifest.json').read_text());m['status']='pending'
    (folder/'manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError,match='completed'): load_dataset(folder)
