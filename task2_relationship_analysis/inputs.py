import json
import re
import os
import stat
from decimal import Decimal
from pathlib import Path
from .contracts import Candidate, InputSnapshot, Issue, InputContractError, InputChangedError, SCHEMA_VERSION, RULE_VERSION
from .serialization import sha256, make_dataset_id, make_record_id

ID_PATTERN = re.compile(r"t\d{8}_\d+\Z")
RELATION_FIELDS = ("project_name", "procurement_unit", "winning_supplier", "project_amount", "bidders")

def safe_path(path: Path, must_exist=True) -> Path:
    """Reject links/junctions at every ancestor before lexical containment checks.

    Windows restricted tokens can read directories but cannot use the handle
    query required by strict realpath. Never fall back until lstat has proved
    that no existing component is a reparse point. Other OS failures propagate.
    """
    absolute = Path(os.path.abspath(path))
    for component in [absolute, *absolute.parents]:
        try:
            metadata = component.lstat()
        except FileNotFoundError:
            if component == absolute and must_exist:
                raise
            continue
        if stat.S_ISLNK(metadata.st_mode) or getattr(metadata, "st_file_attributes", 0) & 0x400:
            raise InputContractError("reparse/link input path forbidden")
    if must_exist and not absolute.exists():
        raise InputContractError("input path does not exist")
    return absolute

def issue(dataset_id, aid, reason, candidate_ids=(), fact_ids=(), locator=None, details=None):
    details = details or {}
    key = (aid, reason, *candidate_ids, *fact_ids, str(locator), json.dumps(details, sort_keys=True, ensure_ascii=False))
    return Issue(dataset_id, make_record_id(dataset_id, "issue", key), aid, reason, list(candidate_ids), list(fact_ids), locator, details)

def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise InputContractError("duplicate JSON key")
        result[key] = value
    return result

def strict_json(raw):
    try:
        return json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_pairs, parse_float=Decimal, parse_constant=lambda x: (_ for _ in ()).throw(InputContractError("nonfinite JSON")))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InputContractError("invalid UTF-8 JSON") from exc

def _rows(path):
    raw = path.read_bytes()
    rows = strict_json(raw)
    if not isinstance(rows, list):
        raise InputContractError("JSON root must be array")
    seen = set()
    for row in rows:
        aid = row.get("announcement_id") if isinstance(row, dict) else None
        if not isinstance(aid, str) or len(aid)>128 or not ID_PATTERN.fullmatch(aid) or aid in seen:
            raise InputContractError("invalid or duplicate announcement ID")
        seen.add(aid)
    return rows, {"sha256": sha256(raw), "bytes": len(raw)}

def _catalog(root):
    result, paths = {}, {}
    for path in sorted(root.iterdir()):
        if path.suffix.lower() not in (".html", ".htm"):
            continue
        if not path.is_file() or safe_path(path).parent != root or len(path.stem)>128 or not ID_PATTERN.fullmatch(path.stem) or path.stem in result:
            raise InputContractError("unsafe HTML input path")
        raw = path.read_bytes()
        result[path.stem] = {"sha256": sha256(raw), "bytes": len(raw)}
        paths[path.stem] = path
    return result, paths

def validate_input_profile(profile):
    if profile is None: return None
    if (not isinstance(profile,dict) or set(profile)!={'kind','producer','extraction_receipt_sha256'} or
        profile['kind']!='uploaded' or not isinstance(profile['producer'],str) or
        not re.fullmatch(r'[A-Za-z][A-Za-z0-9_.:-]{0,127}',profile['producer']) or
        not isinstance(profile['extraction_receipt_sha256'],str) or
        not re.fullmatch(r'[0-9a-f]{64}',profile['extraction_receipt_sha256'])):
        raise InputContractError('invalid uploaded input profile')
    return dict(profile)

def snapshot_inputs(html_root: Path, legacy_path: Path, v11_path: Path, implementation: dict, *, input_profile=None) -> InputSnapshot:
    paths = [Path(html_root), Path(legacy_path), Path(v11_path)]
    if any(p.is_symlink() for p in paths):
        raise InputContractError("linked input root/file forbidden")
    root, old, frozen = [safe_path(p) for p in paths]
    rows, old_meta = _rows(old)
    vrows, vmeta = _rows(frozen)
    catalog, catalog_paths = _catalog(root)
    ids = sorted({r["announcement_id"] for r in rows} | {r["announcement_id"] for r in vrows} | set(catalog))
    html = {aid: catalog.get(aid) for aid in ids}
    manifest = {"legacy": old_meta, "v11": vmeta, "html": html}
    profile=validate_input_profile(input_profile)
    if profile is not None: manifest['input_profile']=profile
    ds = make_dataset_id(manifest, implementation, SCHEMA_VERSION, RULE_VERSION)
    return InputSnapshot(manifest, rows, vrows, {aid: catalog_paths.get(aid) for aid in ids}, ds, old, frozen, root, implementation)

def assert_inputs_unchanged(snapshot: InputSnapshot) -> None:
    try:
        now = snapshot_inputs(snapshot.html_root, snapshot.legacy_path, snapshot.v11_path, snapshot.implementation, input_profile=snapshot.input_manifest.get('input_profile'))
    except (OSError, InputContractError) as exc:
        raise InputChangedError("input no longer readable/valid") from exc
    if now.input_manifest != snapshot.input_manifest:
        raise InputChangedError("input bytes/catalog changed")

def load_candidates(snapshot: InputSnapshot) -> tuple[list[Candidate], list[Issue]]:
    result, issues = [], []
    ds = snapshot.dataset_id
    default_producer=snapshot.input_manifest.get('input_profile',{}).get('producer','legacy_model_unknown')
    def add(aid, file_hash, pointer, field, value, producer=None):
        producer=producer or default_producer
        c = Candidate(ds, make_record_id(ds, "candidate", (file_hash, pointer)), aid, file_hash, pointer, field, value, producer)
        result.append(c)
        return c
    existing = set()
    for number, row in enumerate(snapshot.legacy_rows):
        aid = row["announcement_id"]
        existing.add(aid)
        prefix = f"$[{number}].extra_info"
        extra = row.get("extra_info")
        parent = add(aid, snapshot.input_manifest["legacy"]["sha256"], prefix, "extra_info", extra)
        if not isinstance(extra, dict):
            issues.append(issue(ds, aid, "parse_failed", [parent.candidate_id], details={"expected": "extra object"}))
            continue
        for field in RELATION_FIELDS:
            if field not in extra:
                issues.append(issue(ds, aid, "candidate_absent", [parent.candidate_id], details={"field": field}))
                continue
            value = extra[field]
            c = add(aid, parent.file_sha256, prefix + "." + field, field, value)
            if field == "bidders":
                if not isinstance(value, list):
                    issues.append(issue(ds, aid, "parse_failed", [c.candidate_id]))
                elif not value:
                    issues.append(issue(ds, aid, "participants_not_disclosed", [c.candidate_id]))
                else:
                    for j, name in enumerate(value):
                        child = add(aid, parent.file_sha256, c.json_path + f"[{j}]", "bidder", name)
                        if not isinstance(name, str):
                            issues.append(issue(ds, aid, "parse_failed", [child.candidate_id]))
            elif not isinstance(value, str):
                issues.append(issue(ds, aid, "parse_failed", [c.candidate_id]))
    for aid, path in snapshot.html_entries.items():
        if aid not in existing:
            issues.append(issue(ds, aid, "candidate_absent"))
        if path is None:
            issues.append(issue(ds, aid, "source_missing"))
    for number, row in enumerate(snapshot.v11_rows):
        aid = row["announcement_id"]
        for layer in ("entities", "detail_entities"):
            values = row.get(layer, [])
            if not isinstance(values, list):
                raise InputContractError("V11 entity layer must be array")
            for j, value in enumerate(values):
                if isinstance(value, dict) and value.get("brand"):
                    add(aid, snapshot.input_manifest["v11"]["sha256"], f"$[{number}].{layer}[{j}]", "product_reference", value, default_producer if 'input_profile' in snapshot.input_manifest else "v11_readonly_reference")
    return result, issues
