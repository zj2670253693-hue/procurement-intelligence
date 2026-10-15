"""Create an allowlisted, tracked-code + built-frontend bundle. No private assets.

Official release_assets are deliberately NOT included; a separate server-side
preparation must copy and hash the currently frozen assets without changing them.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import zipfile

MAX_BYTES=64*1024**2
MAX_FILES=10000
CORE_DIRS={'api','data_loader','database','evaluation','extractor','models','output'}
PACKAGES={'task3_platform','task2_relationship_analysis','task2_query_service'}
EXCLUDED={'tests','task3_tests','task2_tests','sample_data','__pycache__','node_modules','venv','runtime','release_assets'}
FRONT_EXTENSIONS={'.html','.js','.css','.svg','.png','.jpg','.jpeg','.ico','.webp','.woff','.woff2','.ttf','.map','.txt'}


def no_links(path):
    for parent in (path,*path.parents):
        if parent.is_symlink() or getattr(parent,'is_junction',lambda:False)():
            raise ValueError('linked_build_input_forbidden')
    return path


def relative_name(name):
    if not isinstance(name,str) or not name or '\\' in name:
        raise ValueError('invalid_bundle_input_path')
    path=PurePosixPath(name)
    if path.is_absolute() or any(part in {'','..'} or ':' in part for part in path.parts):
        raise ValueError('invalid_bundle_input_path')
    return path


def backend_name(name):
    p=relative_name(name);parts=p.parts
    if any(part.startswith('.') or part in EXCLUDED for part in parts):return None
    if p.suffix!='.py' and p.name!='requirements.txt':return None
    if p.name.startswith(('test_','conftest')):return None
    if parts[0]=='task1_entity_extraction' and len(parts)>1:
        if len(parts)==2 or parts[1] in CORE_DIRS:return 'backend/'+PurePosixPath(*parts[1:]).as_posix()
    if parts[0] in PACKAGES and len(parts)>1:return 'backend/'+p.as_posix()
    if parts[:2]==('tools','task1_standard_export') and len(parts)>2:return 'backend/'+p.as_posix()
    return None


def build_bundle(repo,frontend_dist,output,source_commit,tracked_paths):
    repo=no_links(Path(repo).absolute());front=no_links(Path(frontend_dist).absolute());output=no_links(Path(output).absolute())
    if output.exists():raise ValueError('existing_bundle_not_overwritten')
    if not re.fullmatch(r'[a-f0-9]{40}',source_commit):raise ValueError('exact_source_commit_required')
    if not repo.is_dir() or not front.is_dir() or not (front/'index.html').is_file():
        raise ValueError('built_frontend_and_source_required')
    values={};total=0
    def add(name,path):
        nonlocal total
        no_links(path)
        if name in values or not path.is_file():raise ValueError('invalid_bundle_file')
        size=path.stat().st_size
        if size>MAX_BYTES or total+size>MAX_BYTES or len(values)>=MAX_FILES:raise ValueError('bundle_size_limit')
        content=path.read_bytes()
        if len(content)!=size:raise ValueError('build_input_changed')
        total+=size;values[name]=content
    for name in tracked_paths:
        destination=backend_name(name)
        if destination:add(destination,repo/PurePosixPath(name))
    for path in sorted(front.rglob('*')):
        no_links(path)
        relative=path.relative_to(front)
        if any(part.startswith('.') or part in EXCLUDED for part in relative.parts):
            raise ValueError('unexpected_private_frontend_file')
        if path.is_dir():continue
        if path.suffix.lower() not in FRONT_EXTENSIONS:raise ValueError('unexpected_frontend_file_type')
        add('frontend-dist/'+relative.as_posix(),path)
    manifest={'kind':'task3_preview_code_bundle','version':1,'source_commit':source_commit,
        'files':{name:hashlib.sha256(content).hexdigest() for name,content in sorted(values.items())},
        'official_assets_included':False,'new_model_calls':0}
    values['release-manifest.json']=json.dumps(manifest,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    output.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for name,content in sorted(values.items()):
            info=zipfile.ZipInfo(name);info.create_system=3;info.external_attr=0o100644<<16
            info.compress_type=zipfile.ZIP_DEFLATED;archive.writestr(info,content)
    digest=hashlib.sha256()
    with output.open('rb') as stream:
        for block in iter(lambda:stream.read(1024**2),b''):digest.update(block)
    return {'passed':True,'kind':'code_bundle_prepared','source_commit':source_commit,
        'bundle_sha256':digest.hexdigest(),'bundle_bytes':output.stat().st_size,'files':len(manifest['files']),
        'official_assets_included':False,'new_model_calls':0,'task3_deployed':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frontend-dist',required=True)
    parser.add_argument('--output',required=True)
    args=parser.parse_args();repo=Path(__file__).resolve().parents[2]
    def git(*items):
        return subprocess.run(['git','-C',str(repo),*items],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
    try:
        # A dirty tracked source cannot be labelled with the unchanged HEAD hash.
        git('diff','--exit-code','HEAD','--','task1_entity_extraction',*sorted(PACKAGES),'tools/task1_standard_export','frontend')
        commit=git('rev-parse','HEAD').decode('ascii').strip()
        tracked=git('ls-files','-z').decode('utf-8').split('\0')
        report=build_bundle(repo,args.frontend_dist,args.output,commit,tuple(name for name in tracked if name))
        print(json.dumps(report));return 0
    except Exception:
        print(json.dumps({'passed':False,'code':'preview_bundle_failed','task3_deployed':False,'new_model_calls':0}));return 1


if __name__=='__main__':sys.exit(main())
