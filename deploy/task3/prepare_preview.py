"""Prepare a NEW isolated release directory; no DB, env, nginx or service writes.

Use the trusted bundle SHA and the frozen current-release manifest SHA. Failed
partial directories are retained; existing destinations are never overwritten.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import sys
import zipfile

MAX_CODE=64*1024**2
MAX_ASSETS=512*1024**2
MAX_FILES=10000
ASSETS_PREFIX='backend/release_assets/'
CORE={'api','data_loader','database','evaluation','extractor','models','output',
    'task3_platform','task2_relationship_analysis','task2_query_service'}
FRONT={'.html','.js','.css','.svg','.png','.jpg','.jpeg','.ico','.webp','.woff','.woff2','.ttf','.map','.txt'}


def no_links(path):
    for item in (path,*path.parents):
        if item.is_symlink() or getattr(item,'is_junction',lambda:False)():raise ValueError('linked_preview_path_forbidden')
    return path


def file_sha(path):
    digest=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024**2),b''):digest.update(block)
    return digest.hexdigest()


def safe_name(name):
    p=PurePosixPath(name)
    if not name or '\\' in name or p.is_absolute() or not p.parts or any(part in {'..','.'} or ':' in part or part.startswith('.') for part in p.parts):
        raise ValueError('unsafe_preview_member')
    if p.as_posix()!=name:raise ValueError('noncanonical_preview_member')
    return p


def runtime_member(name):
    p=safe_name(name);parts=p.parts
    if any(part in {'tests','sample_data','runtime','release_assets','__pycache__','node_modules'} for part in parts):return False
    if parts[0]=='frontend-dist':return len(parts)>1 and p.suffix.lower() in FRONT
    if parts[0]!='backend' or len(parts)<2 or p.suffix!='.py' and p.name!='requirements.txt':return False
    return len(parts)==2 or parts[1] in CORE or parts[1:3]==('tools','task1_standard_export')


def read_manifest(archive):
    info=archive.getinfo('release-manifest.json')
    if info.file_size>2*1024**2:raise ValueError('bundle_manifest_too_large')
    manifest=json.loads(archive.read(info))
    if (not isinstance(manifest,dict) or manifest.get('kind')!='task3_preview_code_bundle' or manifest.get('version')!=1
        or not isinstance(manifest.get('source_commit'),str) or not re.fullmatch('[a-f0-9]{40}',manifest['source_commit'])
        or manifest.get('official_assets_included') is not False or manifest.get('new_model_calls')!=0
        or not isinstance(manifest.get('files'),dict)):
        raise ValueError('invalid_preview_bundle_manifest')
    return manifest


def validate_bundle(archive):
    entries=archive.infolist();names=[info.filename for info in entries]
    if len(names)>MAX_FILES+1 or len(set(names))!=len(names):raise ValueError('duplicate_or_excess_bundle_members')
    manifest=read_manifest(archive);declared=manifest['files']
    if set(names)!={'release-manifest.json',*declared} or not {'backend/api/main.py','frontend-dist/index.html'}.issubset(declared):
        raise ValueError('preview_bundle_file_list_mismatch')
    total=0
    for name,digest in declared.items():
        if not runtime_member(name) or not isinstance(digest,str) or not re.fullmatch('[a-f0-9]{64}',digest):raise ValueError('forbidden_preview_member')
        info=archive.getinfo(name);mode=info.external_attr>>16
        if info.is_dir() or info.flag_bits&1 or stat.S_IFMT(mode) not in (0,stat.S_IFREG) or info.compress_type not in (zipfile.ZIP_STORED,zipfile.ZIP_DEFLATED):
            raise ValueError('nonregular_preview_member')
        total+=info.file_size
        if info.file_size>MAX_CODE or total>MAX_CODE:raise ValueError('preview_bundle_expanded_limit')
        value=hashlib.sha256();actual=0
        with archive.open(info) as stream:
            for block in iter(lambda:stream.read(1024**2),b''):
                actual+=len(block)
                if actual>info.file_size:raise ValueError('preview_bundle_actual_limit')
                value.update(block)
        if actual!=info.file_size or value.hexdigest()!=digest:raise ValueError('preview_bundle_file_integrity')
    return manifest,total


def prepare_preview(bundle,destination,old_release,*,expected_bundle_sha,expected_old_manifest_sha):
    bundle=no_links(Path(bundle).absolute());target=no_links(Path(destination).absolute());old=no_links(Path(old_release).absolute())
    if target.exists():raise ValueError('existing_preview_not_overwritten')
    if target.is_relative_to(old) or old.is_relative_to(target) or bundle.is_relative_to(target):raise ValueError('preview_input_output_overlap')
    if not target.parent.is_dir():raise ValueError('preview_parent_required')
    parent_manifest=no_links(old/'release-manifest.json')
    if file_sha(bundle)!=expected_bundle_sha or file_sha(parent_manifest)!=expected_old_manifest_sha:raise ValueError('preview_input_sha_mismatch')
    parent=json.loads(parent_manifest.read_bytes());old_files=parent.get('files')
    if not isinstance(old_files,dict):raise ValueError('frozen_parent_file_manifest_required')
    asset_root=no_links(old/'backend/release_assets')
    if not asset_root.is_dir():raise ValueError('frozen_assets_required')
    declared={name:digest for name,digest in old_files.items() if name.startswith(ASSETS_PREFIX)}
    actual={};asset_bytes=0
    for path in sorted(asset_root.rglob('*')):
        no_links(path)
        if path.is_dir():continue
        name=path.relative_to(old).as_posix();safe_name(name)
        if not path.is_file() or len(actual)>=MAX_FILES:raise ValueError('nonregular_or_excess_frozen_assets')
        asset_bytes+=path.stat().st_size
        if asset_bytes>MAX_ASSETS:raise ValueError('frozen_asset_size_limit')
        digest=file_sha(path)
        if declared.get(name)!=digest:raise ValueError('frozen_asset_integrity')
        actual[name]=digest
    if not actual or set(actual)!=set(declared):raise ValueError('frozen_asset_manifest_incomplete')
    with zipfile.ZipFile(bundle) as archive:
        manifest,code_bytes=validate_bundle(archive)
        if shutil.disk_usage(target.parent).free<code_bytes+asset_bytes+4*1024**3:raise ValueError('preview_disk_reserve_required')
        target.mkdir(mode=0o755)
        for name,digest in manifest['files'].items():
            path=target/PurePosixPath(name);path.parent.mkdir(parents=True,exist_ok=True)
            with archive.open(name) as source,path.open('xb') as out:shutil.copyfileobj(source,out,1024**2)
            if file_sha(path)!=digest:raise ValueError('prepared_code_integrity')
    for name,digest in actual.items():
        source=old/PurePosixPath(name);path=target/PurePosixPath(name);path.parent.mkdir(parents=True,exist_ok=True)
        with source.open('rb') as stream,path.open('xb') as out:shutil.copyfileobj(stream,out,1024**2)
        if file_sha(path)!=digest or file_sha(source)!=digest:raise ValueError('frozen_asset_changed_during_copy')
    # Nginx (www-data) must traverse the release to serve frontend-dist; a 750 root
    # makes the HTTPS static path return 500 while the API still answers. Directories
    # only: no other-user write access is granted anywhere.
    if os.name=='posix':
        for directory,_,files in os.walk(target):
            os.chmod(directory,0o755)
            for name in files:os.chmod(os.path.join(directory,name),0o644)
    if file_sha(bundle)!=expected_bundle_sha or file_sha(parent_manifest)!=expected_old_manifest_sha:raise ValueError('preview_inputs_changed')
    full={'kind':'task3_isolated_preview_release','version':1,'source_commit':manifest['source_commit'],
        'code_bundle_sha256':expected_bundle_sha,'official_parent_manifest_sha256':expected_old_manifest_sha,
        'official_task2_dataset_id':parent.get('task2_dataset_id'),'expected_official_counts':parent.get('expected_counts'),
        'files':{**manifest['files'],**actual},'new_model_calls':0,'paid_inference_default':False,
        'full_acceptance_required':True}
    encoded=json.dumps(full,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    with (target/'release-manifest.json').open('xb') as stream:stream.write(encoded)
    return {'passed':True,'stage':'files_prepared','release':str(target),'release_manifest_sha256':hashlib.sha256(encoded).hexdigest(),
        'source_commit':manifest['source_commit'],'official_asset_files':len(actual),'official_asset_bytes':asset_bytes,
        'old_release_changed':False,'new_model_calls':0,'task3_deployed':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',required=True);parser.add_argument('--bundle-sha256',required=True)
    parser.add_argument('--destination',required=True);parser.add_argument('--expected-current-release',required=True)
    parser.add_argument('--old-manifest-sha256',required=True);args=parser.parse_args()
    try:
        releases=Path('/srv/procurement-app/releases');old=Path('/srv/procurement-app/current').resolve(strict=True)
        target=Path(args.destination).absolute()
        if str(old)!=args.expected_current_release or old.parent!=releases or target.parent!=releases or not target.name.startswith('task3-preview-'):
            raise ValueError('explicit_preview_release_bounds_required')
        result=prepare_preview(args.bundle,target,old,expected_bundle_sha=args.bundle_sha256,expected_old_manifest_sha=args.old_manifest_sha256)
        print(json.dumps(result));return 0
    except Exception as error:
        code=str(error) if isinstance(error,ValueError) else 'preview_preparation_failed'
        print(json.dumps({'passed':False,'code':code,'new_model_calls':0,'task3_deployed':False}));return 1


if __name__=='__main__':sys.exit(main())
