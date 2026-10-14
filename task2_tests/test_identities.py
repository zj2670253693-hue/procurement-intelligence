import importlib
from task2_relationship_analysis.contracts import Candidate
from task2_relationship_analysis.source_index import index_html

def candidates(names="甲公司；甲公司分公司", buyer="采购甲", bidders=()):
    data = [("project_name", "测试项目"), ("procurement_unit", buyer), ("winning_supplier", names)] + [("bidder", x) for x in bidders]
    return [Candidate("d", "c" + str(i), "t20260101_1", "s", "$[0]." + str(i), key, value) for i, (key, value) in enumerate(data)]

BASE = '<p>项目编号：ABC</p><p>项目名称：测试项目</p><h6>采购人信息</h6><p>名称：采购甲</p><h6>采购代理机构信息</h6><p>名称：代理乙</p><p>三、中标（成交）信息</p>'
def table(name, lot="合同包1", credit=None):
    extra_h = "<th>统一社会信用代码</th>" if credit else ""
    extra_v = f"<td>{credit}</td>" if credit else ""
    return f'<p>{lot}</p><table><tr><th>供应商名称</th><th>中标金额</th>{extra_h}</tr><tr><td>{name}</td><td>100元</td>{extra_v}</tr></table>'
def run(body, cs=None):
    return importlib.import_module("task2_relationship_analysis.identities").resolve_identities(cs or candidates(), index_html("t20260101_1", body.encode(), "d"), "d")

def test_parent_branch_and_near_names_stay_separate():
    b = run(BASE + table("甲公司") + table("甲公司分公司", "合同包2"))
    names = {f.fields["raw_name"]: f.record_id for f in b.facts if f.kind == "organization"}
    assert names["甲公司"] != names["甲公司分公司"]
    project = next(f for f in b.facts if f.kind == "project")
    assert project.fields["buyer_organization_id"] == names["采购甲"]
    assert "代理乙" not in names

def test_explicit_consortium_keeps_one_subject():
    b = run(BASE + table("甲公司（联合体成员：乙公司）"), candidates("甲公司"))
    groups = [f for f in b.facts if f.kind == "bid_subject"]
    assert len(groups) == 1
    assert groups[0].fields["subject_kind"] == "consortium"
    assert len(groups[0].fields["member_ids"]) == 2

def test_unknown_round_blocks_cross_source_identity():
    b = run(BASE + table("甲公司"), candidates("甲公司"))
    project = next(f for f in b.facts if f.kind == "project")
    assert project.validation_status == "verified"
    assert project.identity_status == "identity_unresolved"
    assert project.aggregation_eligibility == "identity_unresolved"

def test_multi_lot_scope_is_not_array_order():
    b = run(BASE + table("甲公司", "合同包2") + table("甲公司分公司", "合同包1"))
    events = [f for f in b.facts if f.kind == "event"]
    assert {f.fields["lot_raw"] for f in events} >= {"合同包1", "合同包2"}

def test_explicit_credit_code_can_link_aliases():
    b = run(BASE + table("甲公司", credit="913456789012345678") + table("甲新名称", "合同包2", credit="913456789012345678"), candidates("甲公司；甲新名称"))
    companies = [f for f in b.facts if f.kind == "organization" and f.fields["raw_name"] != "采购甲"]
    assert len(companies) == 1
    assert companies[0].fields["identity_basis"]["kind"] == "credit_code"

def test_numbered_project_labels_verify_local_name_only():
    b=run(BASE.replace('项目编号：','一、项目编号：').replace('项目名称：','二、项目名称：')+table('甲公司'),candidates('甲公司'))
    project=next(f for f in b.facts if f.kind=='project')
    assert project.validation_status=='verified' and project.fields['project_code']=='ABC'
    assert project.identity_status=='identity_unresolved'

def test_split_heading_value_and_metadata_table_local_project():
    for metadata in ['<h4><strong>二、项目名称</strong></h4><h4>测试项目</h4>', '<table><tr><td>采购项目名称</td><td>测试项目</td></tr></table>']:
        body=BASE.replace('<p>项目名称：测试项目</p>',metadata)+table('甲公司')
        b=run(body,candidates('甲公司'))
        assert next(f for f in b.facts if f.kind=='project').validation_status=='verified'
