"""Import CLI must preserve the same source-proven copies as the pipeline."""
import json

import pytest

import import_to_db
from database.dao import DataRepository
from models.result_io import serialize_extraction_result
from models.schemas import DetailedEntity, ExtractedEntity, ExtractionResult
from tests.test_dual_layer_persistence import RecordingCursor, make_database


HTML = '''<table><tr><td>公告信息</td></tr>
<tr><td>采购项目名称</td><td>虚构食材采购</td></tr></table>
<div class="content"><p>项目名称：虚构食材采购</p>
<p>三、中标信息</p><p>供应商名称：虚构农场</p><p>中标金额：未公示</p>
<p>四、主要标的信息</p><table>
<tr><th>序号</th><th>标项名称</th><th>标的名称</th><th>品牌</th><th>规格型号</th><th>数量</th><th>单价(元)</th></tr>
<tr><td>5</td><td>3标段样例鲜果</td><td>样例鲜果</td><td>/</td><td>新鲜一级</td><td>一批</td><td>0</td></tr>
<tr><td>6</td><td>4标段样例鲜果</td><td>样例鲜果</td><td>/</td><td>新鲜一级</td><td>一批</td><td>0</td></tr>
</table><p>五、评审专家名单</p><p>虚构专家</p></div>'''


def run_import(tmp_path, monkeypatch, *, html=HTML, supply_dir=True, repeat=False, details=()):
    """Only replace the external MySQL transport; real parser/DAO/CLI stay live."""
    output = tmp_path / "output"
    output.mkdir()
    result = ExtractionResult(announcement_id="synthetic-import-copy", detail_status="available" if details else "no_attachment",
        detail_entities=list(details),
        entities=[ExtractedEntity(product_name="样例鲜果", unit_price="0", quantity="一批") for _ in range(2)])
    (output / "raw_results.json").write_text(json.dumps([serialize_extraction_result(result)], ensure_ascii=False), encoding="utf-8")
    source = tmp_path / "html"
    source.mkdir()
    if html is not None:
        (source / "synthetic-import-copy.html").write_text(html, encoding="utf-8")
    database = make_database()

    class ImportCursor(RecordingCursor):
        def execute(self, sql, params=None):
            super().execute(sql, params)
            if "select count(*) as cnt from extracted_entities" in sql.lower():
                batches = [rows for statement, rows in self.connection.statements
                           if statement.startswith("INSERT INTO extracted_entities")]
                self.rows = [{"cnt": len(batches[-1]) if batches else 0}]
            elif sql.startswith("SELECT * FROM announcements ORDER BY"):
                self.rows = [{"announcement_id": "synthetic-import-copy"}]

    monkeypatch.setattr(database._conn, "cursor", lambda: ImportCursor(database._conn))
    monkeypatch.setattr(database, "connect", lambda **kwargs: database)
    monkeypatch.setattr(database, "close", lambda: None)
    monkeypatch.setattr(import_to_db, "DatabaseManager", lambda config: database)
    monkeypatch.setattr(import_to_db.config, "OUTPUT_DIR", output)
    monkeypatch.setattr(import_to_db.sys, "argv", ["import_to_db.py"] + ([str(source)] if supply_dir else []))
    import_to_db.main()
    if repeat:
        import_to_db.main()
    return database


def entity_batches(database):
    return [rows for sql, rows in database._conn.statements if sql.startswith("INSERT INTO extracted_entities")]


def detail_batches(database):
    return [rows for sql, rows in database._conn.statements if sql.startswith("INSERT INTO extracted_entity_details")]


def test_import_preserves_two_source_proven_cross_package_copies(tmp_path, monkeypatch):
    database = run_import(tmp_path, monkeypatch)
    assert entity_batches(database) == [[
        ("synthetic-import-copy", "样例鲜果", "", "", "", "0", "一批", ""),
        ("synthetic-import-copy", "样例鲜果", "", "", "", "0", "一批", ""),
    ]]
    assert database._conn.begins == 1


def test_repeated_import_preserves_copy_multiplicity_each_time(tmp_path, monkeypatch):
    database = run_import(tmp_path, monkeypatch, repeat=True)
    assert [len(rows) for rows in entity_batches(database)] == [2, 2]
    assert sum(sql.startswith("DELETE FROM extracted_entities") for sql, _ in database._conn.statements) == 2


@pytest.mark.parametrize("mode", ["no-directory", "missing-html", "ambiguous-source", "parse-error"])
def test_unproven_import_retains_conservative_dedup(tmp_path, monkeypatch, mode):
    if mode == "parse-error":
        def fail_parse(*args, **kwargs):
            raise OSError("synthetic unreadable HTML")
        monkeypatch.setattr(import_to_db.HTMLAnnouncementParser, "parse_file", fail_parse)
    database = run_import(tmp_path, monkeypatch,
        supply_dir=mode != "no-directory",
        html=None if mode == "missing-html" else HTML.replace("4标段样例鲜果", "3标段样例鲜果") if mode == "ambiguous-source" else HTML)
    assert [len(rows) for rows in entity_batches(database)] == [1]


def detail(page, filename="虚构清单.pdf", **changes):
    values = dict(package_name="采购包一", product_name="虚构电缆", category="电缆",
                  brand="虚构品牌", spec_model="4芯", unit_price="2元", quantity="3米",
                  total_price="6元", source_file=filename, source_page=page)
    values.update(changes)
    return DetailedEntity(**values)


def test_import_retains_distinct_complete_pages_in_one_attachment(tmp_path, monkeypatch):
    # A page-blind DAO would lose the second observable provenance record.
    database = run_import(tmp_path, monkeypatch, repeat=True,
                          details=[detail("10"), detail("11"), detail("10")])
    expected = [
        ("synthetic-import-copy", "采购包一", "虚构电缆", "电缆", "虚构品牌", "4芯", "2元", "3米", "6元", "虚构清单.pdf", "10"),
        ("synthetic-import-copy", "采购包一", "虚构电缆", "电缆", "虚构品牌", "4芯", "2元", "3米", "6元", "虚构清单.pdf", "11"),
    ]
    assert detail_batches(database) == [expected, expected]


@pytest.mark.parametrize("pages", [
    ("10", "10"), ("10", " 10 "), ("10", ""), ("10", "未公示"),
    ("10", "10-11"), ("10", "第11页"), ("10", "11,12"),
    ("10", "0"), ("10", "-1"), ("10", "11.0"), ("10", "１１"),
])
def test_import_keeps_old_dedup_for_same_or_incomplete_pages(tmp_path, monkeypatch, pages):
    database = run_import(tmp_path, monkeypatch, details=[detail(p) for p in pages])
    assert [len(rows) for rows in detail_batches(database)] == [1]
    assert detail_batches(database)[0][0][-1] == pages[0]


@pytest.mark.parametrize("filename", ["另一附件.pdf", ""])
def test_import_does_not_pair_different_or_missing_attachments(tmp_path, monkeypatch, filename):
    database = run_import(tmp_path, monkeypatch, details=[detail("10"), detail("11", filename)])
    assert [len(rows) for rows in detail_batches(database)] == [1]


def test_import_does_not_promote_valid_pages_in_an_ambiguous_group(tmp_path, monkeypatch):
    database = run_import(tmp_path, monkeypatch, details=[detail("10"), detail("11"), detail("")])
    assert [len(rows) for rows in detail_batches(database)] == [1]


def test_import_page_policy_uses_existing_normalized_content_key(tmp_path, monkeypatch):
    database = run_import(tmp_path, monkeypatch, details=[detail("10"),
        detail("10", product_name="虚构 电缆", spec_model="４芯"), detail("11")])
    assert [len(rows) for rows in detail_batches(database)] == [2]
    assert [r[-1] for r in detail_batches(database)[0]] == ["10", "11"]


def test_normal_repository_keeps_its_page_blind_dedup():
    database = make_database()
    result = ExtractionResult(announcement_id="synthetic-import-copy", detail_status="available",
                              detail_entities=[detail("10"), detail("11")])
    DataRepository(database).save_extraction_result(result)
    assert [len(rows) for rows in detail_batches(database)] == [1]
