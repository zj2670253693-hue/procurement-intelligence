"""QA-only R6/current comparison: metadata linkage is not a field/price rewrite."""
from collections import Counter
import json
from pathlib import Path
import sys

from standard_export import HEADERS, LABELS, encoded, require, sha_file
from source_name_join import cosmetic_name_key, select_name_metadata

HERE = Path(__file__).resolve().parent


def read(directory, name):
    return json.loads((directory / name).read_text(encoding="utf-8"))


def audit(directory):
    old = HERE / "candidate-r6-context-final"
    previous, current = read(old, "verification.json"), read(directory, "verification.json")
    for name, digest in previous["artifacts_sha256"].items():
        require(sha_file(old / name) == digest, "R6 artifact changed: " + name)
    for file, digest in previous["input_fingerprints"].items():
        path = Path(file)
        protected = HERE / "r6-code-final" / path.name if path.parent == HERE else path
        require(sha_file(protected) == digest, "R6 baseline/input changed: " + str(path))
    for file, digest in current["input_fingerprints"].items():
        require(sha_file(file) == digest, "current protected input changed: " + file)
    before, after = read(old, "tables.json"), read(directory, "tables.json")
    require(before["headers"] == after["headers"] == HEADERS, "headers changed")
    require(before["optional_headers"] == after["optional_headers"], "header roles changed")
    for name in ("总表", "投标人表"):
        require(before["tables"][name] == after["tables"][name], "other table changed")
    require(read(old, "uuid-map.json") == read(directory, "uuid-map.json"), "UUID registry changed")
    require(read(old, "source-coverage-audit.json") == read(directory, "source-coverage-audit.json"), "legacy audit or money path changed")
    old_docs, new_docs = read(old, "advanced-and-original-results.json"), read(directory, "advanced-and-original-results.json")
    require(len(old_docs) == len(new_docs) == 1038, "wrong announcement count")
    sources, raw_entities = {}, {}
    for original, revised in zip(old_docs, new_docs):
        aid = revised["announcement_id"]
        require(original["announcement_id"] == aid and original["classification"] == revised["classification"], "identity/class changed")
        require(original["raw_result"] == revised["raw_result"], "raw/base/detail data changed")
        a, b = original["source_projection"], revised["source_projection"]
        require({k: v for k, v in a.items() if k != "item_bindings"}
                == {k: v for k, v in b.items() if k != "item_bindings"}, "unapproved source projection change")
        require(len(a["item_bindings"]) == len(b["item_bindings"]), "binding occurrences changed")
        for old_binding, new_binding in zip(a["item_bindings"], b["item_bindings"]):
            require(old_binding == {k: v for k, v in new_binding.items() if k != "source_identity"}, "old binding/units changed")
        sources[aid] = b
        raw_entities[aid] = revised["raw_result"]["entities"]
    joins = read(directory, "name-metadata-joins.json")
    join_map = {(r["announcement_id"], r["entity_index"]): r for r in joins}
    require(len(join_map) == len(joins), "duplicate join audit")
    mappings = {(r["announcement_id"], r["package_key"]): r for r in read(directory, "uuid-map.json")}
    old_rows, new_rows = before["tables"]["标的表"], after["tables"]["标的表"]
    require(len(old_rows) == len(new_rows) == 6776, "item count changed")
    changed, occurrence = [], {}
    for original, revised in zip(old_rows, new_rows):
        aid = original["原公告ID"]
        index = occurrence.get(aid, 0); occurrence[aid] = index + 1
        require(all(original[label] == revised[label] for label in LABELS), "one of seven fields changed")
        differences = {k for k in original if original[k] != revised[k]}
        require(differences <= {"UUID", "标段/合同包", "证据来源", "备注"}, "unapproved field change")
        record = join_map.get((aid, index))
        if not differences:
            require(record is None, "join audit with no metadata change")
            continue
        require(record is not None and record["new_money_binding_used"] is False, "missing metadata-only audit")
        require(record["raw_product_name"] == raw_entities[aid][index]["product_name"], "audit name mismatch")
        require(original["标段/合同包"] in {"", "未分包"}, "existing package overwritten")
        proof = record["source_identity"]
        require(proof["proved"] is True, "unproved row used")
        require(cosmetic_name_key(record["raw_product_name"]) == cosmetic_name_key(proof["complete_name"]),
                "unapproved name normalization used")
        raw = raw_entities[aid][index]
        counts = Counter(cosmetic_name_key(e.get("product_name")) for e in raw_entities[aid])
        accepted = {a.get("package_key", "") for a in sources[aid]["awards"]
                    if a.get("supplier") and (aid, a.get("package_key")) in mappings}
        match = select_name_metadata({f: raw.get(f, "") for f in ("product_name", "brand", "spec_model")},
                                     sources[aid]["item_bindings"], counts, accepted)
        require(match and match["source_identity"] == proof and match["package_key"] == record["package_key"],
                "new join failed unique literal-source eligibility check")
        expected_evidence = record["source_evidence"] + "；仅空白/圆括号名称对齐，未据此改写七字段"
        require(revised["证据来源"] == expected_evidence, "unexpected evidence output")
        key = record["package_key"]
        if key:
            expected = mappings[(aid, key)]
            require(revised["UUID"] == expected["UUID"] and revised["标段/合同包"] == expected["标段/合同包"], "wrong package identity")
            require(any(a["package_key"] == key for a in sources[aid]["awards"]), "unaccepted package filled")
            require(original["备注"].removeprefix("采购包对应关系未知").lstrip("；") == revised["备注"], "money/other notes changed")
        else:
            require(original["UUID"] == revised["UUID"] and original["标段/合同包"] == revised["标段/合同包"]
                    and original["备注"] == revised["备注"], "unknown identity was changed")
        changed.append({**record, "row": revised["序号"], "UUID": revised["UUID"]})
    require(len(changed) == len(joins), "audit/output cardinality mismatch")
    require(current["new_model_calls"] == 0 and not current["database_accessed"] and not current["online_cutover_done"], "scope violation")
    return {"passed": True, "comparison_only": True, "announcements": 1038,
            "metadata_changed_rows": len(changed), "changed_announcements": len({r["announcement_id"] for r in changed}),
            "new_known_package_rows": sum(bool(r["package_key"]) for r in changed), "changes": changed,
            "all_seven_fields_and_prices_unchanged": True, "legacy_money_path_and_audit_unchanged": True,
            "totals_bidders_uuid_registry_unchanged": True, "raw_details_classification_unchanged": True,
            "R6_and_V12_source_baselines_unchanged": True, "new_model_calls": 0, "database_accessed": False}


if __name__ == "__main__":
    require(sys.dont_write_bytecode, "use Python -B")
    directory = (HERE / sys.argv[1]).resolve()
    require(directory.is_relative_to(HERE), "outside V13")
    report = audit(directory)
    with (directory / "name-metadata-diff-verification.json").open("xb") as stream:
        stream.write(encoded(report))
    print(json.dumps({k: v for k, v in report.items() if k != "changes"}, ensure_ascii=False))
