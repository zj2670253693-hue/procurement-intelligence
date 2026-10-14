"""Overlapping citation suffixes cannot depend on a set's iteration order."""
import pytest

from extractor import summary_rules
from models.schemas import ExtractedEntity


def set_reference_order(monkeypatch, short_first):
    pair = ["见附件", "详见附件"] if short_first else ["详见附件", "见附件"]
    remaining = sorted(set(summary_rules._REFERENCE_VALUES) - set(pair))
    # Control only the nondeterministic input order; the real label parser,
    # collapse and cleanup are exercised unchanged.
    monkeypatch.setattr(summary_rules, "_REFERENCE_VALUES", pair + remaining)


@pytest.mark.parametrize("short_first", [True, False])
@pytest.mark.parametrize("source,want", [
    ("咨询服务（第1包）详见附件", "咨询服务(第1包)"),
    ("咨询服务（第1包）见附件", "咨询服务(第1包)"),
    ("见附件研究服务（第1包）", "见附件研究服务(第1包)"),
])
def test_overlapping_reference_suffix_is_removed_independently_of_order(monkeypatch, short_first, source, want):
    set_reference_order(monkeypatch, short_first)
    assert summary_rules._package_labels(source) == [want]


@pytest.mark.parametrize("short_first", [True, False])
def test_complete_summary_package_name_does_not_retain_reference_fragment(monkeypatch, short_first):
    set_reference_order(monkeypatch, short_first)
    source = (
        "项目名称：咨询服务\n三、中标信息\n供应商名称：甲公司\n中标金额：12000元\n"
        "四、主要标的信息\n1 | 甲公司 | 咨询服务（第1包）详见附件 | 详见附件\n"
    )
    rows = [ExtractedEntity(product_name="附件设备", brand="甲公司", unit_price="100元")]
    before = [row.model_dump() for row in rows]
    result = summary_rules.sanitize_summary_entities(rows, detail_status="missing", source_text=source)
    assert [row.product_name for row in result] == ["咨询服务(第1包)"]
    assert [row.total_price for row in result] == ["12000元"]
    assert all(not row.spec_model and not row.unit_price for row in result)
    assert [row.model_dump() for row in rows] == before
