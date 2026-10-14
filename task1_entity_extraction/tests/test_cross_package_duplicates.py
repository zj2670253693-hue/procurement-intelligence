"""Source multiplicity is not a guessed raw-row-to-package assignment."""
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from database.dao import DataRepository
from data_loader.html_parser import HTMLAnnouncementParser
from extractor.summary_rules import build_summary_source, sanitize_summary_entities
from models.schemas import AnnouncementContent, ExtractedEntity, ExtractionResult
from pipeline import EntityExtractionPipeline
from tests.test_dual_layer_persistence import make_database
from tests.test_dual_layer_pipeline import RecordingExtractor, make_pipeline, write_attachment_zip, write_html
import pipeline as pipeline_module


NAME = "样例鲜果"
PROJECT = "虚构食材采购"
HEADER = ["序号", "标项名称", "标的名称", "品牌", "规格型号", "数量", "单价(元)"]
ROWS = [["5", "3标段" + NAME, NAME, "/", "新鲜一级", "一批", "0"],
        ["6", "4标段" + NAME, NAME, "/", "新鲜一级", "一批", "0"]]


def model(**changes):
    return ExtractedEntity(**(dict(product_name=NAME, unit_price="0", quantity="一批") | changes))


def source(*, rows=None, body_rows=None, table_rows=None, header=None, layout="pipe",
           tail="", project=PROJECT, body_project=PROJECT, title="四、主要标的信息", extra_tables=None):
    header = HEADER if header is None else header
    rows = ROWS if rows is None else rows
    body_rows = rows if body_rows is None else body_rows
    table_rows = rows if table_rows is None else table_rows
    serialize = (lambda row: " | ".join(row)) if layout == "pipe" else (lambda row: "\n".join(row))
    body = (f"项目名称：{body_project}\n三、中标信息\n供应商名称：虚构农场\n"
            "中标金额：未公示\n" + title + "\n"
            + "\n".join(serialize(row) for row in [header, *body_rows])
            + "\n" + tail + "\n五、评审专家名单\n虚构专家")
    tables = [[["公告信息"], ["采购项目名称", project]], [header, *table_rows]]
    return build_summary_source(body, tables + (extra_tables or []))


def clean(rows, text):
    return sanitize_summary_entities(rows, detail_status="no_attachment", source_text=text)


@pytest.mark.parametrize("layout", ["pipe", "vertical"])
def test_two_explicit_packages_preserve_two_existing_unchanged_raw_copies(layout):
    originals = [model(), model()]
    before = [row.model_dump() for row in originals]
    result = clean(originals, source(layout=layout))
    assert [row.model_dump() for row in result] == before
    assert [row.model_dump() for row in originals] == before


def test_vertical_body_can_split_one_complete_spec_cell_across_text_lines():
    rows = [ROWS[0].copy(), ROWS[1].copy()]
    rows[0][4] = rows[1][4] = "新鲜一级规格标准完整"
    text = source(rows=rows, layout="vertical").replace("新鲜一级规格标准完整\n", "新鲜一级\n规格标准\n完整\n", 1)
    assert [row.model_dump() for row in clean([model(), model()], text)] == [model().model_dump(), model().model_dump()]


def test_protected_copies_do_not_reorder_or_modify_other_summary_rows():
    originals = [model(product_name="其他食材"), model(), model()]
    result = clean(originals, source())
    assert [row.model_dump() for row in result] == [row.model_dump() for row in originals]


def test_interleaved_original_copies_retain_raw_relative_order_without_package_assignment():
    originals = [model(), model(product_name="其他食材"), model()]
    assert [row.model_dump() for row in clean(originals, source())] == [row.model_dump() for row in originals]


def test_interleaved_copies_with_transformed_unlocatable_anchor_defer_to_old_cleanup():
    originals = [model(), model(product_name="其他食材", brand="详见附件"), model()]
    assert [row.model_dump() for row in clean(originals, source())] == [model().model_dump(), model(product_name="其他食材").model_dump()]


@pytest.mark.parametrize("count", [1, 3])
def test_source_two_cannot_create_missing_copy_or_trim_three_to_two(count):
    assert len(clean([model() for _ in range(count)], source())) == 1


@pytest.mark.parametrize("changes", [{"spec_model": "冲突规格"}, {"brand": "冲突品牌"},
                                   {"quantity": "二批"}, {"unit_price": "9"}])
def test_model_attribute_conflicts_do_not_authorize_multiplicity(changes):
    assert len(clean([model(**changes), model(**changes)], source())) == 1


@pytest.mark.parametrize("column,value", [(3, "另一品牌"), (4, "另一规格"), (5, "二批"), (6, "9")])
def test_same_named_packages_with_different_source_attributes_remain_ambiguous(column, value):
    rows = [ROWS[0].copy(), ROWS[1].copy()]
    rows[1][column] = value
    assert len(clean([model(), model()], source(rows=rows))) == 1


@pytest.mark.parametrize("side", ["body", "table"])
def test_source_missing_independent_row_cannot_prove_count(side):
    kwargs = {"body_rows" if side == "body" else "table_rows": [ROWS[0]]}
    assert len(clean([model(), model()], source(**kwargs))) == 1


@pytest.mark.parametrize("side", ["body", "table"])
def test_inconsistent_body_and_serialized_attributes_fail_closed(side):
    changed = [ROWS[0].copy(), ROWS[1].copy()]
    changed[1][4] = "其他规格"
    kwargs = {"body_rows" if side == "body" else "table_rows": changed}
    assert len(clean([model(), model()], source(**kwargs))) == 1


def test_same_package_two_physical_rows_are_not_two_packages():
    rows = [ROWS[0].copy(), ROWS[1].copy()]
    rows[1][1] = rows[0][1]
    assert len(clean([model(), model()], source(rows=rows))) == 1


def test_identical_body_row_repeated_is_not_independent_package_evidence():
    assert len(clean([model(), model()], source(rows=[ROWS[0], ROWS[0]]))) == 1


def test_parent_child_serialization_repeats_do_not_inflate_independent_count():
    text = source(rows=[ROWS[0]], extra_tables=[[HEADER, ROWS[0]]])
    assert len(clean([model(), model()], text)) == 1


def test_repeated_serialized_witness_of_two_real_rows_does_not_double_count():
    assert len(clean([model(), model()], source(extra_tables=[[HEADER, *ROWS]]))) == 2


@pytest.mark.parametrize("label", ["十一标段", "第十一包"])
def test_unsupported_chinese_package_alias_cannot_fake_distinct_package(label):
    rows = [ROWS[0].copy(), ROWS[1].copy()]
    rows[0][1], rows[1][1] = label + NAME, "11标段" + NAME
    assert len(clean([model(), model()], source(rows=rows))) == 1


def test_sequence_numbers_without_explicit_package_header_are_not_package_identity():
    header = HEADER.copy()
    header[1] = "序列描述"
    assert len(clean([model(), model()], source(header=header))) == 1


@pytest.mark.parametrize("title", ["四、采购需求", "四、候选人名单", "四、代理服务收费标准及金额"])
def test_non_awarded_item_section_cannot_prove_package_count(title):
    assert len(clean([model(), model()], source(title=title))) == 1


@pytest.mark.parametrize("tail", [NAME, "未知标的声明：" + NAME, "主要标的信息为候选报价"])
def test_unparsed_same_name_or_negative_statement_remains_veto(tail):
    assert len(clean([model(), model()], source(tail=tail))) == 1


def test_project_scope_conflict_rejects_multiplicity():
    assert len(clean([model(), model()], source(project="另一项目"))) == 1


def test_validated_project_title_containing_item_name_is_not_a_competing_item_row():
    project = NAME + "及其他食材采购"
    text = source(project=project, body_project=project)
    assert len(clean([model(), model()], text)) == 2


def test_unknown_metadata_same_name_declaration_is_not_a_harmless_title():
    text = source(extra_tables=[[["公告信息"], ["采购项目名称", PROJECT], ["未知标的声明", NAME]]])
    assert len(clean([model(), model()], text)) == 1


def test_incomplete_source_row_rejects_multiplicity():
    assert len(clean([model(), model()], source(rows=[ROWS[0], ROWS[1][:-1]]))) == 1


def test_reference_cleanup_collision_is_not_original_identical_raw_group():
    originals = [model(brand="详见附件"), model(brand="见附件")]
    assert len(clean(originals, source())) == 1


def test_price_cleanup_collision_is_not_original_identical_raw_group():
    originals = [model(total_price="13元"), model(total_price="14元")]
    assert len(clean(originals, source())) == 1


def test_original_group_whose_fields_change_under_old_cleanup_is_not_restored():
    assert len(clean([model(brand="详见附件"), model(brand="详见附件")], source())) == 1


@pytest.mark.parametrize("context", ["合同包9:", "货物类(另一供应商)"])
def test_unresolved_external_package_or_supplier_scope_cannot_be_ignored(context):
    text = source().replace("四、主要标的信息\n", "四、主要标的信息\n" + context + "\n")
    assert len(clean([model(), model()], text)) == 1


def test_chinese_and_numeric_aliases_of_same_package_are_not_distinct():
    rows = [ROWS[0].copy(), ROWS[1].copy()]
    rows[0][1], rows[1][1] = "第三包" + NAME, "采购包3" + NAME
    assert len(clean([model(), model()], source(rows=rows))) == 1


@pytest.mark.parametrize("labels", [("采购包3", "标包3"), ("标包3", "标包03")])
def test_numeric_package_namespace_and_leading_zero_aliases_do_not_supply_capacity(labels):
    rows = [ROWS[0].copy(), ROWS[1].copy()]
    rows[0][1], rows[1][1] = labels
    assert len(clean([model(), model()], source(rows=rows))) == 1


@pytest.mark.parametrize("tail", ["注：第3标段和第4标段属于同一采购包", "所属采购包：第九包", "标段：9"])
def test_unconsumed_package_ownership_claim_vetoes_even_without_item_name(tail):
    assert len(clean([model(), model()], source(tail=tail))) == 1


def test_supplementary_same_package_disclaimer_vetoes_table_group():
    text = source().replace("\n\n--- HTML表格1 ---", "\n六、其他补充事宜\n上述主要标的信息中的第3标段和第4标段属于同一采购包\n\n--- HTML表格1 ---")
    assert len(clean([model(), model()], text)) == 1


def test_proven_package_header_and_other_complete_item_rows_are_not_unparsed_claims():
    header = HEADER.copy()
    header[1] = "采购包"
    rows = [ROWS[0].copy(), ROWS[1].copy()]
    rows[0][1], rows[1][1] = "3", "4"
    third = ["7", "5", "其他食材", "/", "另一规格", "一批", "0"]
    assert len(clean([model(), model()], source(header=header, rows=[*rows, third]))) == 2


def test_normalized_but_not_literal_identical_raw_rows_do_not_authorize_copies():
    assert len(clean([model(), model(quantity="一 批")], source())) == 1


@pytest.mark.parametrize("column", [3, 4, 5, 6])
def test_blank_source_attribute_is_not_complete_evidence(column):
    rows = [ROWS[0].copy(), ROWS[1].copy()]
    rows[0][column] = rows[1][column] = ""
    assert len(clean([model(), model()], source(rows=rows))) == 1


def test_unknown_or_duplicate_header_cannot_hide_source_attribute_role():
    for changed in ["其他规格描述", "标项名称"]:
        header = HEADER.copy()
        header[4] = changed
        assert len(clean([model(), model()], source(header=header))) == 1


def test_attachment_only_rows_cannot_prove_summary_multiplicity():
    text = source(rows=[]) + "\n\n--- 附件：采购包清单.txt ---\n" + source()
    assert len(clean([model(), model()], text)) == 1


def test_unknown_source_marker_cannot_certify_table_rows():
    text = source().replace("--- HTML表格2 ---", "--- 未知来源2 ---")
    assert len(clean([model(), model()], text)) == 1


@pytest.mark.parametrize("structure", ["nested", "rowspan", "colspan"])
def test_real_html_nested_and_merged_cells_cannot_fake_aligned_independent_rows(structure):
    table = "<table><tr>" + "".join("<th>" + h + "</th>" for h in HEADER) + "</tr>"
    for index, row in enumerate(ROWS):
        table += "<tr>"
        for column, cell in enumerate(row):
            if structure == "rowspan" and column == 4:
                if index == 1:
                    continue
                table += '<td rowspan="2">' + cell + '</td>'
            elif structure == "colspan" and column in {3, 4}:
                if column == 4:
                    continue
                table += '<td colspan="2">' + row[3] + row[4] + '</td>'
            else:
                table += "<td>" + cell + "</td>"
        table += "</tr>"
    table += "</table>"
    if structure == "nested":
        table = "<table><tr><td>" + table + "</td></tr></table>"
    html = ('<table><tr><td>公告信息</td></tr><tr><td>采购项目名称</td><td>' + PROJECT
            + '</td></tr></table><div class="content"><p>项目名称：' + PROJECT
            + '</p><p>三、中标信息</p><p>供应商名称：虚构农场</p><p>中标金额：未公示</p>'
            + '<p>四、主要标的信息</p>' + table + '<p>五、评审专家名单</p></div>')
    content = HTMLAnnouncementParser().parse_html(html)
    text = build_summary_source(content.html_text, content.html_tables)
    assert len(clean([model(), model()], text)) == 1


def test_pipeline_database_save_revalidates_primary_source_and_keeps_both_existing_rows():
    database = make_database()
    instance = object.__new__(EntityExtractionPipeline)
    instance.repository = DataRepository(database)
    instance._db_lock = threading.Lock()
    content = AnnouncementContent(announcement_id="synthetic-source-count", html_text=source().split("\n\n--- HTML表格1 ---")[0],
        html_tables=[[["公告信息"], ["采购项目名称", PROJECT]], [HEADER, *ROWS]])
    result = ExtractionResult(announcement_id=content.announcement_id, detail_status="no_attachment", entities=[model(), model()])
    instance._finish_single_result(result, content, Path("synthetic-source-count.html"))
    inserts = [values for sql, values in database._conn.statements if sql.startswith("INSERT INTO extracted_entities")]
    assert len(inserts) == 1 and len(inserts[0]) == 2
    assert inserts[0][0] == inserts[0][1] == ("synthetic-source-count", NAME, "", "", "", "0", "一批", "")
    assert database._conn.commits == 1


def test_repository_without_proof_still_deduplicates_even_if_raw_text_contains_packages():
    database = make_database()
    result = ExtractionResult(announcement_id="synthetic-no-proof", detail_status="no_attachment", entities=[model(), model()])
    DataRepository(database).save_extraction_result(result, raw_text=source())
    inserts = [values for sql, values in database._conn.statements if sql.startswith("INSERT INTO extracted_entities")]
    assert len(inserts[0]) == 1


def test_uninspectable_legacy_repository_callable_still_receives_original_save_arguments():
    saved = {}
    instance = object.__new__(EntityExtractionPipeline)
    instance.repository = SimpleNamespace(save_extraction_result=saved.update)
    instance._db_lock = threading.Lock()
    content = AnnouncementContent(announcement_id="synthetic-opaque", title="虚构标题", html_text="虚构正文")
    result = ExtractionResult(announcement_id=content.announcement_id, detail_status="no_attachment")
    failure = None
    try:
        instance._finish_single_result(result, content, Path("synthetic-opaque.html"))
    except Exception as error:
        failure = type(error).__name__
    assert failure is None, "signature inspection must not break an old callable"
    assert saved == dict(result=result, title="虚构标题", raw_text="虚构正文", file_path="synthetic-opaque.html")


def test_new_rule_invalidates_summary_cache_but_reuses_attachment_results(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline_module, "CROSS_PACKAGE_DUPLICATE_RULE_VERSION", "fixture-v1", raising=False)
    html = write_html(tmp_path, "synthetic-count-cache", "采购包一成交结果")
    write_attachment_zip(html)
    extractor = RecordingExtractor(detail_count=3)
    instance = make_pipeline(tmp_path, extractor)
    first = instance.process_single_file(str(html), run_mode="both")
    extractor.summary_calls.clear()
    extractor.detail_calls.clear()
    monkeypatch.setattr(pipeline_module, "CROSS_PACKAGE_DUPLICATE_RULE_VERSION", "fixture-v2", raising=False)
    changed = instance.process_single_file(str(html), run_mode="both", previous_result=first, resume=True)
    assert len(extractor.summary_calls) == 1
    assert extractor.detail_calls == []
    assert changed.detail_input_hash == first.detail_input_hash
    assert changed.detail_entities == first.detail_entities
