import importlib
from dataclasses import replace

def test_nfkc_maps_to_raw_quote():
    from task2_relationship_analysis.contracts import Candidate
    s = importlib.import_module("task2_relationship_analysis.source_index")
    e = importlib.import_module("task2_relationship_analysis.evidence")
    i = s.index_html("t20260101_1", "<p>Ａ<span>Ｂ</span>ﬃ</p>".encode(), "d")
    c = Candidate("d", "c", i.announcement_id, "sha", "$[0].x", "bidder", "ABffi")
    proofs, issues = e.locate_candidate(c, i)
    assert len(proofs) == 1
    assert proofs[0].raw_quote == "Ａ<span>Ｂ</span>ﬃ"
    assert e.verify_locator(proofs[0], i)
    assert not e.verify_locator(replace(proofs[0], raw_quote="fabricated"), i)

def test_multiple_occurrences_are_not_silently_first():
    from task2_relationship_analysis.contracts import Candidate
    s = importlib.import_module("task2_relationship_analysis.source_index")
    e = importlib.import_module("task2_relationship_analysis.evidence")
    i = s.index_html("t20260101_1", b"<p>A</p><p>A</p>", "d")
    proofs, _ = e.locate_candidate(Candidate("d", "c", i.announcement_id, "s", "$[0].x", "bidder", "A"), i)
    assert len(proofs) == 2

def test_bad_table_cell_is_not_evidence():
    from task2_relationship_analysis.contracts import Candidate
    s = importlib.import_module("task2_relationship_analysis.source_index")
    e = importlib.import_module("task2_relationship_analysis.evidence")
    i = s.index_html("t20260101_1", b"<table><tr><td>A<table><tr><td>B</td></tr></table></td></tr></table>", "d")
    proofs, issues = e.locate_candidate(Candidate("d", "c", i.announcement_id, "s", "$[0].x", "bidder", "A"), i)
    assert proofs == []
