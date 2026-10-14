import importlib
import pytest

def index(raw):
    return importlib.import_module("task2_relationship_analysis.source_index").index_html("t20260101_1", raw, "d")

@pytest.mark.parametrize("raw,encoding", [("<p>项目名称：测试</p>".encode(), "utf-8"), (b'\xef\xbb\xbf<p>test</p>', "utf-8-sig"), ('<meta charset="GB18030"><p>项目名称：测试</p>'.encode("gb18030"), "gb18030")])
def test_encoding_is_strict(raw, encoding):
    i = index(raw)
    assert i.encoding == encoding
    assert not i.diagnostics

def test_illegal_encoding_never_ignored():
    i = index(b'<meta charset="utf-8"><p>\xff</p>')
    assert "decode_failed" in {x.reason_code for x in i.diagnostics}
    assert i.sections == []

def test_spans_keep_physical_parent():
    i = index(b'<table><tr><th>name</th></tr><tr><td rowspan="2">company</td></tr><tr><td>price</td></tr></table>')
    assert len(i.tables[0].rows) == 3
    assert i.tables[0].rows[1][0].rowspan == 2
    assert i.tables[0].rows[1][0].text == "company"
    assert len([c for row in i.tables[0].rows for c in row if c.text == "company"]) == 1

def test_malformed_nested_table_is_not_verified():
    i = index(b'<table><tr><td>A<table><tr><td>B</td></tr></table></td></tr></table>')
    assert any(t.malformed for t in i.tables)
    assert "parse_failed" in {x.reason_code for x in i.diagnostics}

def test_script_style_not_indexed():
    i = index(b'<script>hidden</script><style>hidden</style><p>visible</p>')
    assert "hidden" not in "".join(s.text for s in i.sections)
