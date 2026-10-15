import hashlib
import json
import zipfile
import pytest
from task3_tests.test_deployment import load


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def inputs(tmp_path,extra=None):
    old=tmp_path/'old';asset=old/'backend/release_assets/html/t20990101_1.html'
    asset.parent.mkdir(parents=True);asset.write_bytes(b'frozen-official-source')
    manifest={'files':{'backend/release_assets/html/t20990101_1.html':sha(asset)},
        'expected_counts':{'announcements':1038,'entities':6784,'detail_entities':14110},'task2_dataset_id':'ds_'+'f'*64}
    old_manifest=old/'release-manifest.json';old_manifest.write_text(json.dumps(manifest),encoding='utf-8')
    bundle=tmp_path/'code.zip'
    files={'backend/api/main.py':b'# code','frontend-dist/index.html':b'<div id="app"></div>',**(extra or {})}
    code_manifest={'kind':'task3_preview_code_bundle','version':1,'source_commit':'a'*40,
        'files':{name:hashlib.sha256(data).hexdigest() for name,data in files.items()},'official_assets_included':False,'new_model_calls':0}
    with zipfile.ZipFile(bundle,'w') as z:
        z.writestr('release-manifest.json',json.dumps(code_manifest))
        for name,data in files.items():z.writestr(name,data)
    return bundle,old,old_manifest,asset


def test_preparation_verifies_code_and_copies_only_hash_verified_frozen_assets(tmp_path):
    m=load('prepare_preview');bundle,old,old_manifest,asset=inputs(tmp_path)
    env=old/'backend/.env';env.write_bytes(b'never-copy-existing-credentials')
    destination=tmp_path/'preview'
    before=sha(old_manifest),sha(asset),env.read_bytes()
    report=m.prepare_preview(bundle,destination,old,expected_bundle_sha=sha(bundle),expected_old_manifest_sha=sha(old_manifest))
    assert report['passed'] and report['stage']=='files_prepared' and report['new_model_calls']==0
    assert report['task3_deployed'] is False and report['official_asset_files']==1
    assert (destination/'backend/release_assets/html/t20990101_1.html').read_bytes()==b'frozen-official-source'
    assert not (destination/'backend/.env').exists()
    full=json.loads((destination/'release-manifest.json').read_bytes())
    assert len(full['files'])==3 and full['official_parent_manifest_sha256']==before[0]
    assert (sha(old_manifest),sha(asset),env.read_bytes())==before


@pytest.mark.parametrize('change',['bundle_hash','parent_hash','frozen_bytes','unregistered_asset','existing_destination'])
def test_bad_preparation_never_modifies_official_or_existing_preview(tmp_path,change):
    m=load('prepare_preview');bundle,old,old_manifest,asset=inputs(tmp_path);destination=tmp_path/'preview'
    bundle_hash,parent_hash=sha(bundle),sha(old_manifest)
    if change=='bundle_hash':bundle_hash='0'*64
    if change=='parent_hash':parent_hash='0'*64
    if change=='frozen_bytes':asset.write_bytes(b'changed-original')
    if change=='unregistered_asset':(asset.parent/'unregistered.html').write_bytes(b'not-in-manifest')
    if change=='existing_destination':destination.mkdir();(destination/'keep').write_bytes(b'preserve-me')
    before=sha(old_manifest),sha(asset)
    with pytest.raises(ValueError):m.prepare_preview(bundle,destination,old,expected_bundle_sha=bundle_hash,expected_old_manifest_sha=parent_hash)
    assert (sha(old_manifest),sha(asset))==before
    if change=='existing_destination':assert (destination/'keep').read_bytes()==b'preserve-me'
    else:assert not destination.exists()


@pytest.mark.parametrize('path',['../escape.py','backend/.env','backend/release_assets/private.json','frontend-dist/.env'])
def test_bundle_manifest_cannot_smuggle_private_files_or_escape_paths(tmp_path,path):
    m=load('prepare_preview');bundle,old,parent,asset=inputs(tmp_path,{path:b'not-allowed'})
    destination=tmp_path/'preview'
    with pytest.raises(ValueError):m.prepare_preview(bundle,destination,old,expected_bundle_sha=sha(bundle),expected_old_manifest_sha=sha(parent))
    assert not destination.exists() and asset.read_bytes()==b'frozen-official-source'
