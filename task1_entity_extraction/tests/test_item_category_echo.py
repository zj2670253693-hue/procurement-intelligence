"""Same-row category text is not a second procurement item.

Fictional fixtures exercise the public summary path. Real duplicate owners,
unknown declarations and incomplete witnesses must still reject restoration.
"""
import pytest

from extractor.summary_rules import build_summary_source, sanitize_summary_entities
from models.schemas import ExtractedEntity


PROJECT = "虚构检测设备采购"
NAME = "样例测量仪"
HEADER = ["品目号", "品目名称", "采购标的", "品牌", "规格型号", "数量(单位)", "单价(元)", "总价(元)"]
CELLS = ["2-1", NAME, NAME, "样例品牌", "ZX-9", "2(套)", "4550", "9100"]


def original(**changes):
    values = dict(product_name=NAME, category=NAME, brand="样例品牌",
                  spec_model="ZX-9", unit_price="4550", quantity="2套", total_price="9100")
    return ExtractedEntity(**(values | changes))


def source(*, body_rows=None, table_rows=None, header=None, layout="vertical", tail="", project=PROJECT):
    header = HEADER if header is None else header
    body_rows = [CELLS] if body_rows is None else body_rows
    table_rows = body_rows if table_rows is None else table_rows
    def serialize(row):
        return " | ".join(row) if layout == "pipe" else "\n".join(row)
    body = (f"项目名称：{PROJECT}\n三、采购结果\n合同包2(虚构设备):\n"
            "供应商名称：甲公司\n中标（成交）金额：91000元\n四、主要标的信息\n"
            "合同包2(虚构设备):\n货物类(甲公司)\n"
            + "\n".join(serialize(row) for row in [header, *body_rows])
            + "\n" + tail + "\n五、评审专家名单\n虚构专家")
    return build_summary_source(body, [[["公告信息"], ["采购项目名称", project]], [header, *table_rows]])


def clean(rows, text):
    return sanitize_summary_entities(rows, detail_status="no_attachment", source_text=text)


@pytest.mark.parametrize("layout", ["vertical", "pipe"])
@pytest.mark.parametrize("total", ["9100", "9,100.00元", "0.9100万元"])
def test_complete_same_row_category_echo_preserves_equivalent_original_total_and_spelling(layout, total):
    row = original(total_price=total)
    before = row.model_dump()
    result = clean([row], source(layout=layout))
    assert len(result) == 1
    assert result[0].model_dump() == before
    assert row.model_dump() == before


def test_same_named_category_allows_unique_explicit_item_total_under_v12():
    row = original(total_price="")
    assert clean([row], source())[0].model_dump() == (row.model_dump() | {"total_price": "9100元"})


@pytest.mark.parametrize("changes", [{"brand": "其他品牌"}, {"spec_model": "ZX-8"}, {"total_price": "9200"}])
def test_category_echo_cannot_remove_brand_spec_or_amount_conflict(changes):
    result = clean([original(**changes)], source())
    assert len(result) == 1
    assert result[0].total_price == ("9100元" if changes == {"total_price": "9200"} else "")


def test_actual_repeated_item_rows_remain_ambiguous_after_category_echo_handling():
    second = ["2-2", *CELLS[1:]]
    assert clean([original()], source(body_rows=[CELLS, second]))[0].total_price == ""


def test_same_name_in_two_packages_cannot_be_selected_by_matching_amount():
    text = source()
    second = ["3-1", *CELLS[1:]]
    new_body = "合同包3(另一设备):\n货物类(甲公司)\n" + "\n".join([*HEADER, *second])
    text = text.replace("五、评审专家名单", new_body + "\n五、评审专家名单")
    text += "\n\n--- HTML表格3 ---\n" + "\n".join(" | ".join(row) for row in [HEADER, second])
    assert clean([original()], text)[0].total_price == ""


def test_unparsed_same_name_statement_is_still_a_veto():
    assert clean([original()], source(tail=NAME))[0].total_price == ""


@pytest.mark.parametrize("tail", [
    "采购标的：" + NAME,
    "标的名称: " + NAME,
    "名称：" + NAME,
    "合同包3(另一设备):\n货物类(乙公司)\n采购标的：" + NAME,
    "未知字段：" + NAME,
    "采购标的：" + NAME + "；规格型号：ZX-8",
    "未知标的声明：" + NAME + "（另一规格）",
    "3-1 | 采购标的：" + NAME + " | 未解析型号",
])
def test_labeled_unparsed_same_name_claim_cannot_be_hidden_by_category_echo(tail):
    assert clean([original()], source(tail=tail))[0].total_price == ""


def test_same_value_in_brand_is_not_an_authorized_category_echo():
    cells = CELLS.copy()
    cells[3] = NAME
    assert clean([original(brand=NAME)], source(body_rows=[cells]))[0].total_price == ""


def test_unknown_category_header_is_not_a_proven_item_category_role():
    header = HEADER.copy()
    header[1] = "其他描述"
    assert clean([original()], source(header=header))[0].total_price == ""


@pytest.mark.parametrize("side", ["body", "table"])
def test_inconsistent_body_and_table_row_cannot_authorize_category_echo(side):
    changed = CELLS.copy()
    changed[-1] = "9200"
    text = source(body_rows=[changed], table_rows=[CELLS]) if side == "body" else source(table_rows=[changed])
    assert clean([original()], text)[0].total_price == ""


def test_partial_body_competitor_retains_negative_name_evidence():
    assert clean([original()], source(body_rows=[CELLS, ["2-2", *CELLS[1:-1]]], table_rows=[CELLS], layout="pipe"))[0].total_price == ""


def test_detached_serialized_competitor_is_not_ignored():
    assert clean([original()], source(table_rows=[CELLS, ["2-2", *CELLS[1:]]]))[0].total_price == ""


def test_category_echo_requires_complete_cells_even_when_serializations_agree():
    cells = CELLS.copy()
    cells[-2] = ""
    assert clean([original()], source(body_rows=[cells], layout="pipe"))[0].total_price == ""


def test_project_metadata_mismatch_still_rejects_restoration():
    assert clean([original()], source(project="虚构其他项目"))[0].total_price == ""


def test_source_header_without_currency_cannot_borrow_model_currency():
    header = HEADER.copy()
    header[-1] = "总价"
    assert clean([original(total_price="9100元")], source(header=header))[0].total_price == ""


def test_budget_amount_header_is_not_a_total_role():
    header = HEADER.copy()
    header[-1] = "预算金额(元)"
    assert clean([original()], source(header=header))[0].total_price == ""


def test_duplicate_serialized_rows_do_not_become_unique_by_echo_handling():
    assert clean([original()], source(table_rows=[CELLS, CELLS]))[0].total_price == ""


@pytest.mark.parametrize("mode", ["summary", "both", "legacy"])
def test_item_total_rule_version_invalidates_base_cache_without_invalidating_detail(tmp_path, monkeypatch, mode):
    import pipeline as pipeline_module
    from tests.test_dual_layer_pipeline import RecordingExtractor, make_pipeline, write_html, write_attachment_zip
    from tests.test_legacy_base_layer import _RecordingExtractor, _pipeline
    html = write_html(tmp_path, "fictional-item-echo-resume-" + mode, "采购包一")
    write_attachment_zip(html)
    extractor = _RecordingExtractor() if mode == "legacy" else RecordingExtractor()
    pipe = _pipeline(tmp_path, extractor) if mode == "legacy" else make_pipeline(tmp_path, extractor)
    first = pipe.process_single_file(str(html), run_mode=mode)
    monkeypatch.setattr(pipeline_module, "ITEM_TOTAL_RULE_VERSION", "fictional-next-item-total-rule", raising=False)
    second = pipe.process_single_file(str(html), run_mode=mode, previous_result=first, resume=True)
    calls = extractor.legacy_calls if mode == "legacy" else extractor.summary_calls
    assert len(calls) == 2
    assert first.summary_input_hash != second.summary_input_hash
    assert first.detail_input_hash == second.detail_input_hash
    if mode == "both":
        assert len(extractor.detail_calls) == 1
