import importlib
import json
import pytest

def api():
    return importlib.import_module("task2_relationship_analysis.inputs")

@pytest.mark.parametrize("raw", ['{}', 'null', '[{"announcement_id":"../escape"}]', '[{"announcement_id":"t20260101_1","announcement_id":"t20260101_2"}]', '[{"announcement_id":"t20260101_1"},{"announcement_id":"t20260101_1"}]'])
def test_strict_json_and_ids(input_case, raw):
    a = api()
    from task2_relationship_analysis.contracts import InputContractError
    root, old, frozen = input_case()
    old.write_text(raw, encoding="utf-8")
    with pytest.raises(InputContractError):
        a.snapshot_inputs(root, old, frozen, {})

def test_candidates_preserve_absent_empty_and_types(input_case):
    a = api()
    root, old, frozen = input_case({"winning_supplier": "甲公司；乙公司", "bidders": [], "project_amount": None})
    snap = a.snapshot_inputs(root, old, frozen, {})
    cs, issues = a.load_candidates(snap)
    by = {c.field_kind: c for c in cs}
    assert by["winning_supplier"].raw_value == "甲公司；乙公司"
    assert by["project_amount"].raw_value is None
    assert by["bidders"].raw_value == []
    assert {i.reason_code for i in issues} >= {"candidate_absent", "participants_not_disclosed", "parse_failed"}
    root2, old2, frozen2 = input_case({"bidders": ["甲", 1]})
    cs2, issues2 = a.load_candidates(a.snapshot_inputs(root2, old2, frozen2, {}))
    assert any(c.json_path.endswith("bidders[1]") and c.raw_value == 1 for c in cs2)
    assert "parse_failed" in {i.reason_code for i in issues2}

def test_source_snapshot_change(input_case):
    a = api()
    from task2_relationship_analysis.contracts import InputChangedError
    root, old, frozen = input_case()
    (root / "unused.zip").write_bytes(b"not read")
    snap = a.snapshot_inputs(root, old, frozen, {})
    assert len(snap.input_manifest["html"]) == 1
    a.assert_inputs_unchanged(snap)
    (root / "t20260101_1.html").write_bytes(b"changed")
    with pytest.raises(InputChangedError):
        a.assert_inputs_unchanged(snap)

def test_missing_source_stays_diagnostic(input_case):
    a = api()
    root, old, frozen = input_case()
    (root / "t20260101_1.html").unlink()
    snap = a.snapshot_inputs(root, old, frozen, {})
    assert snap.html_entries["t20260101_1"] is None
    cs, issues = a.load_candidates(snap)
    assert "source_missing" in {i.reason_code for i in issues}

def test_no_alias_link_escape(input_case, local_tmp):
    a = api()
    from task2_relationship_analysis.contracts import InputContractError
    root, old, frozen = input_case()
    linked = local_tmp / "linked-root"
    try:
        linked.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("OS does not grant symlink creation")
    with pytest.raises(InputContractError):
        a.snapshot_inputs(linked, old, frozen, {})
