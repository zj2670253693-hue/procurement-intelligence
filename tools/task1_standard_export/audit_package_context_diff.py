"""Offline QA only: compare R5 against saved R4 without changing either input."""
import json
from pathlib import Path
import sys

from standard_export import HEADERS, LABELS, encoded, require, sha_file

HERE = Path(__file__).resolve().parent


def read(directory, name):
    return json.loads((directory / name).read_text(encoding="utf-8"))


def audit(directory):
    old = HERE / "candidate-r4-final"
    previous = read(old, "verification.json")
    current = read(directory, "verification.json")
    for name, digest in previous["artifacts_sha256"].items():
        require(sha_file(old / name) == digest, "saved R4 data changed: " + name)
    for file, digest in previous["input_fingerprints"].items():
        path = Path(file)
        protected = HERE / "r4-code-baseline" / path.name if path.parent == HERE else path
        require(sha_file(protected) == digest, "R4 protected input/baseline changed: " + str(path))
    for file, digest in current["input_fingerprints"].items():
        require(sha_file(file) == digest, "R5 protected input changed: " + file)
    before, after = read(old, "tables.json"), read(directory, "tables.json")
    require(before["headers"] == after["headers"] == HEADERS, "headers changed")
    require(before["optional_headers"] == after["optional_headers"], "header roles changed")
    for sheet in ("总表", "投标人表"):
        require(before["tables"][sheet] == after["tables"][sheet], "unapproved table change: " + sheet)
    require(read(old, "uuid-map.json") == read(directory, "uuid-map.json"), "registered UUID mapping changed")
    require(read(old, "source-coverage-audit.json") == read(directory, "source-coverage-audit.json"), "existing source audit changed")
    originals, revised = read(old, "advanced-and-original-results.json"), read(directory, "advanced-and-original-results.json")
    require(len(originals) == len(revised) == 1038, "wrong original count")
    sources = {}
    for old_doc, new_doc in zip(originals, revised):
        require(old_doc["announcement_id"] == new_doc["announcement_id"], "document order changed")
        require(old_doc["raw_result"] == new_doc["raw_result"], "raw/base/detail results changed")
        require(old_doc["classification"] == new_doc["classification"], "classification changed")
        old_source, new_source = old_doc["source_projection"], new_doc["source_projection"]
        require({k: v for k, v in old_source.items() if k != "item_bindings"}
                == {k: v for k, v in new_source.items() if k != "item_bindings"}, "other source projection changed")
        sources[new_doc["announcement_id"]] = new_source
    old_rows, new_rows = before["tables"]["标的表"], after["tables"]["标的表"]
    require(len(old_rows) == len(new_rows) == 6776, "item count changed")
    mappings = {(r["announcement_id"], r["package_key"]): r for r in read(directory, "uuid-map.json")}
    changed = []
    allowed = {"UUID", "标段/合同包", "证据来源", "备注"}
    for original, revised in zip(old_rows, new_rows):
        require(all(original[h] == revised[h] for h in LABELS), "one of seven fields changed")
        differences = {k for k in original if original[k] != revised[k]}
        require(differences <= allowed, "unapproved item field change")
        if not differences:
            continue
        require(original["标段/合同包"] == "", "existing known package overwritten")
        aid, name = revised["原公告ID"], revised["产品服务名称"]
        bindings = [b for b in sources[aid]["item_bindings"] if b["product_name"] == name]
        require(len(bindings) == 1 and "package_context_evidence" in bindings[0], "missing unique recovery evidence")
        proof = bindings[0]["package_context_evidence"]
        mapping = mappings[(aid, proof["package_key"])]
        require(revised["UUID"] == mapping["UUID"] and revised["标段/合同包"] == mapping["标段/合同包"], "wrong shared identity")
        require(any(a["package_key"] == proof["package_key"] for a in sources[aid]["awards"]), "no accepted package award")
        require(revised["证据来源"] == original["证据来源"] + "；唯一成交包标题上下文", "evidence text changed beyond annotation")
        require(original["备注"].removeprefix("采购包对应关系未知").lstrip("；") == revised["备注"], "other remarks changed")
        changed.append({"announcement_id": aid, "product_name": name, "source_caption": proof["source_caption"],
                        "package_key": proof["package_key"], "UUID": revised["UUID"], "row": revised["序号"]})
    require(current["new_model_calls"] == 0 and not current["database_accessed"] and not current["online_cutover_done"], "scope flags changed")
    return {"passed": True, "comparison_only": True, "announcements": 1038, "changed_item_rows": len(changed),
            "changed_announcements": len({r["announcement_id"] for r in changed}), "changes": changed,
            "all_seven_fields_unchanged": True, "total_and_bidder_tables_unchanged": True,
            "raw_and_details_unchanged": True, "existing_uuid_map_unchanged": True,
            "R4_data_and_code_baseline_unchanged": True, "V12_and_source_inputs_unchanged": True,
            "new_model_calls": 0, "database_accessed": False}


if __name__ == "__main__":
    require(sys.dont_write_bytecode, "use Python -B")
    directory = (HERE / sys.argv[1]).resolve()
    require(directory.is_relative_to(HERE), "outside V13")
    report = audit(directory)
    with (directory / "package-context-diff-verification.json").open("xb") as stream:
        stream.write(encoded(report))
    print(json.dumps({k: v for k, v in report.items() if k != "changes"}, ensure_ascii=False))
