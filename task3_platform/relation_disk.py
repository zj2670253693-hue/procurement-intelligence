"""Durable ownership of shared relation bytes, including failed build staging."""
import re
from .contracts import PlatformError, strict_json_loads, validate_task_id
from .paths import reject_link_ancestors


def staging_root(paths,task_id):
    validate_task_id(task_id)
    return reject_link_ancestors(paths.root/'task2/staging'/task_id)


def owned_versions(paths,task_id):
    index=reject_link_ancestors(paths.task(task_id).extraction/'relation-ownership.json')
    if not index.exists(): return []
    if index.stat().st_size>64*1024: raise PlatformError('relation_ownership_invalid',409)
    data=strict_json_loads(index.read_bytes())
    versions=data.get('versions') if isinstance(data,dict) else None
    if (not isinstance(versions,list) or len(versions)>256 or
            any(not isinstance(v,str) or not re.fullmatch(r'ds_[a-f0-9]{64}',v) for v in versions) or
            len(versions)!=len(set(versions))):
        raise PlatformError('relation_ownership_invalid',409)
    return versions


def counted_roots(paths,task_id):
    roots=[staging_root(paths,task_id)]
    for version in owned_versions(paths,task_id):
        roots.extend((reject_link_ancestors(paths.root/'task2/artifacts'/version),
                      reject_link_ancestors(paths.root/'task2/run_receipts'/(version+'.inputs.json'))))
    return roots


def promote(paths,task_id,receipt):
    from .uploads import _hash_file
    from .worker import atomic_json
    from task2_query_service.artifacts import load_dataset
    version=receipt['dataset_id']
    if not isinstance(version,str) or not re.fullmatch(r'ds_[a-f0-9]{64}',version):
        raise PlatformError('relation_ownership_invalid',409)
    source=reject_link_ancestors(staging_root(paths,task_id)/'artifacts'/version)
    target=reject_link_ancestors(paths.root/'task2/artifacts'/version)
    receipt_target=reject_link_ancestors(paths.root/'task2/run_receipts'/(version+'.inputs.json'))
    target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if target.exists():
        load_dataset(target)
        if _hash_file(target/'manifest.json')!=receipt['manifest_sha256']:
            raise PlatformError('relation_shared_version_conflict',409)
        # Keep this task's staging (and charge it). The first publisher retains
        # ownership of the immutable shared target; no automatic deletion occurs.
        if receipt_target.exists(): return target
    versions=owned_versions(paths,task_id)
    if version not in versions:
        if len(versions)>=256: raise PlatformError('relation_ownership_limit',413)
        versions.append(version)
    # Persist ownership BEFORE the atomic directory move. A crash on either side
    # is accounted from staging or shared output after the next process starts.
    index=paths.task(task_id).extraction/'relation-ownership.json'
    index.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    atomic_json(index,{'versions':sorted(versions)})
    if not receipt_target.exists():
        receipt_source=reject_link_ancestors(staging_root(paths,task_id)/'run_receipts'/(version+'.inputs.json'))
        if receipt_source.stat().st_size>64*1024: raise PlatformError('relation_receipt_invalid',409)
        receipt_target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with receipt_target.open('xb') as stream: stream.write(receipt_source.read_bytes())
    if not target.exists(): source.rename(target)
    return target
