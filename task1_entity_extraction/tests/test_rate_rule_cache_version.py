"""A rate cleanup change invalidates summary only, not attachment inference."""
import pipeline as pipeline_module

from tests.test_dual_layer_pipeline import RecordingExtractor, make_pipeline, write_attachment_zip, write_html


def test_changed_rate_rule_recomputes_summary_but_reuses_all_details(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_module, "AWARDED_RATE_TIER_RULE_VERSION", "fixture-version-one", raising=False)
    html = write_html(tmp_path, "synthetic-cache", "采购包一成交结果")
    write_attachment_zip(html)
    extractor = RecordingExtractor(detail_count=3)
    instance = make_pipeline(tmp_path, extractor)
    first = instance.process_single_file(str(html), run_mode="both")
    same = instance.process_single_file(str(html), run_mode="both", previous_result=first, resume=True)
    assert same.summary_input_hash == first.summary_input_hash
    assert len(extractor.summary_calls) == len(extractor.detail_calls) == 1
    extractor.summary_calls.clear()
    extractor.detail_calls.clear()
    monkeypatch.setattr(pipeline_module, "AWARDED_RATE_TIER_RULE_VERSION", "fixture-version-two", raising=False)
    changed = instance.process_single_file(str(html), run_mode="both", previous_result=same, resume=True)
    assert len(extractor.summary_calls) == 1
    assert extractor.detail_calls == []
    assert changed.summary_input_hash != first.summary_input_hash
    assert changed.detail_input_hash == first.detail_input_hash
    assert changed.detail_entities == first.detail_entities
