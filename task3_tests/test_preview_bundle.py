import hashlib
import json
from pathlib import Path
import zipfile
import pytest
from task3_tests.test_deployment import load


def test_preview_bundle_flattens_runtime_packages_without_private_or_untracked_data(tmp_path):
    m=load('build_preview_bundle');repo=tmp_path/'repo';repo.mkdir()
    sources={
        'task1_entity_extraction/config.py':b'# synthetic config\n',
        'task1_entity_extraction/api/main.py':b'# synthetic api\n',
        'task1_entity_extraction/output/writer.py':b'# synthetic writer\n',
        'task3_platform/runtime.py':b'# synthetic worker\n',
        'task2_relationship_analysis/builder.py':b'# synthetic relations\n',
        'task2_query_service/integration.py':b'# synthetic query\n',
        'tools/task1_standard_export/standard_export.py':b'# synthetic exports\n',
        'task1_entity_extraction/.env':b'synthetic-private-secret',
        'task1_entity_extraction/output/raw_results.json':b'synthetic-private-results',
        'task1_entity_extraction/sample_data/example.html':b'synthetic-private-source',
        'task3_platform/tests/test_not_runtime.py':b'# test\n'}
    for name,data in sources.items():
        p=repo/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    untracked=repo/'task3_platform/private_plugin.py';untracked.write_bytes(b'synthetic-untracked')
    front=tmp_path/'dist';front.mkdir();(front/'index.html').write_bytes(b'<div id="app"></div>')
    (front/'assets').mkdir();(front/'assets/main.js').write_bytes(b'/* synthetic frontend */')
    output=tmp_path/'preview.zip'
    report=m.build_bundle(repo,front,output,'a'*40,tuple(sources))
    assert report['passed'] is True and report['task3_deployed'] is False and report['new_model_calls']==0
    with zipfile.ZipFile(output) as z:
        names=set(z.namelist());manifest=json.loads(z.read('release-manifest.json'))
        assert names=={'release-manifest.json','backend/config.py','backend/api/main.py','backend/output/writer.py',
            'backend/task3_platform/runtime.py','backend/task2_relationship_analysis/builder.py',
            'backend/task2_query_service/integration.py','backend/tools/task1_standard_export/standard_export.py',
            'frontend-dist/index.html','frontend-dist/assets/main.js'}
        assert manifest['source_commit']=='a'*40 and len(manifest['files'])==9
        assert all(hashlib.sha256(z.read(name)).hexdigest()==digest for name,digest in manifest['files'].items())
        assert b'synthetic-private' not in b''.join(z.read(name) for name in names)


def test_preview_bundle_refuses_overwrite_missing_frontend_and_path_escape(tmp_path):
    m=load('build_preview_bundle');repo=tmp_path/'repo';repo.mkdir();front=tmp_path/'dist';front.mkdir()
    output=tmp_path/'exists.zip';output.write_bytes(b'preserve-old-artifact')
    with pytest.raises(ValueError):m.build_bundle(repo,front,output,'b'*40,())
    assert output.read_bytes()==b'preserve-old-artifact'
    with pytest.raises(ValueError):m.build_bundle(repo,front,tmp_path/'missing-index.zip','b'*40,())
    (front/'index.html').write_bytes(b'index')
    with pytest.raises(ValueError):m.build_bundle(repo,front,tmp_path/'escape.zip','b'*40,('../outside.py',))
    assert not (tmp_path/'escape.zip').exists()


def test_preview_bundle_does_not_package_frontend_environment_files(tmp_path):
    m=load('build_preview_bundle');repo=tmp_path/'repo';repo.mkdir();front=tmp_path/'dist';front.mkdir()
    (front/'index.html').write_bytes(b'index');(front/'.env').write_bytes(b'never-upload')
    with pytest.raises(ValueError):m.build_bundle(repo,front,tmp_path/'bad-dist.zip','b'*40,())
    assert not (tmp_path/'bad-dist.zip').exists()
