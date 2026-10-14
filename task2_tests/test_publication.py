import importlib
from pathlib import Path
import pytest
from task2_tests.test_builder import build
from task2_relationship_analysis.contracts import ArtifactConflictError

def test_atomic_retry_and_existing_conflict(input_case,local_tmp):
    result,paths=build(input_case,local_tmp)
    second=importlib.import_module('task2_relationship_analysis.builder').build_dataset(*paths)
    assert second.dataset_id==result.dataset_id and second.status=='reused'
    assert second.manifest==result.manifest
    target=Path(result.output_dir)/'facts.jsonl'
    target.write_bytes(target.read_bytes()+b'corruption')
    with pytest.raises(ArtifactConflictError): importlib.import_module('task2_relationship_analysis.builder').build_dataset(*paths)

def test_interrupted_publication_never_completed(input_case,local_tmp,monkeypatch):
    module=importlib.import_module('task2_relationship_analysis.publication')
    def fail(*a,**k): raise OSError('simulated interruption')
    monkeypatch.setattr(module,'_write_file',fail)
    with pytest.raises(OSError): build(input_case,local_tmp)
    assert not list((local_tmp/'outputs').glob('ds_*/manifest.json'))
