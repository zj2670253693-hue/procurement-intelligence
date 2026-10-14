"""Physical-source positives for the previously undelivered Task2 profiles."""
from pathlib import Path
from task2_tests.test_identities import BASE, table, candidates
from task2_relationship_analysis.contracts import Candidate
from task2_relationship_analysis.source_index import index_html
from task2_relationship_analysis.identities import resolve_identities
from task2_relationship_analysis.relations import verify_relations
from task2_relationship_analysis.amounts import verify_amounts


def build(body, cs):
    index = index_html("t20260101_1", body.encode(), "d")
    identities = resolve_identities(cs, index, "d")
    return verify_amounts(cs, index, verify_relations(cs, index, identities))


def reference(name="设备甲", brand="品牌甲"):
    return Candidate("d", "prod", "t20260101_1", "s", "$[0].entities[0]", "product_reference", {"product_name": name, "brand": brand})


def goods(extra_header, extra_value):
    return ("<p>四、主要标的信息 合同包1</p><p>货物类（甲公司）</p><table><tr><th>采购标的</th><th>品牌</th>"
            + extra_header + "</tr><tr><td>设备甲</td><td>品牌甲</td>" + extra_value + "</tr></table>")


def test_explicit_manufacturer_is_a_supply_organization_not_brand_or_award_supplier():
    body = BASE + table("甲公司") + goods("<th>生产厂家</th>", "<td>乙制造有限公司</td>")
    b = build(body, candidates("甲公司") + [reference()])
    supply = next(f for f in b.facts if f.kind == "supply" and f.fields["provider_kind"] == "product_supplier")
    provider = next(f for f in b.facts if f.record_id == supply.fields["provider_organization_id"])
    assert provider.fields["raw_name"] == "乙制造有限公司"
    assert provider.validation_status == supply.validation_status == "verified"
    assert not any(f.kind == "award" and f.fields["subject_id"] == provider.record_id for f in b.facts)
    assert supply.aggregation_eligibility != "eligible"


def test_plain_brand_is_not_a_manufacturer_even_when_company_shaped():
    body = BASE + table("甲公司") + goods("<th>规格型号</th>", "<td>X1</td>")
    b = build(body, candidates("甲公司") + [reference()])
    assert not any(f.kind == "supply" and f.fields["provider_kind"] == "product_supplier" for f in b.facts)


def test_explicit_manufacturer_prefix_allows_technical_tail_but_not_a_conflicting_second_declaration():
    for detail, expected in [
        ("此项产品无品牌，制造商名称：乙制造有限公司。1.满足食品标准。", ["乙制造有限公司"]),
        ("此项产品无品牌，制造商名称：乙制造有限公司。制造商名称：丙制造有限公司。", []),
    ]:
        b = build(BASE + table("甲公司") + goods("<th>规格型号</th>", "<td>"+detail+"</td>"), candidates("甲公司")+[reference()])
        names = [next(f.fields["raw_name"] for f in b.facts if f.record_id==s.fields["provider_organization_id"])
                 for s in b.facts if s.kind=="supply" and s.fields["provider_kind"]=="product_supplier"]
        assert names == expected


def test_explicit_line_total_is_not_calculated_from_unit_quantity_or_package_total():
    body = BASE + table("甲公司") + goods("<th>单价(元)</th><th>数量</th><th>总价(元)</th>", "<td>10</td><td>99</td><td>20</td>")
    b = build(body, candidates("甲公司") + [reference()])
    line = next(f for f in b.facts if f.kind == "amount" and f.fields["amount_kind"] == "product_line_total")
    assert line.fields["decimal_value"] == "20"
    assert line.fields["product_candidate_id"] == "prod"
    assert line.fields["owner_subject_id"] is not None
    assert line.fields["coverage_status"] == "unknown_overlap"
    assert line.aggregation_eligibility != "eligible"


def test_no_line_total_when_only_unit_quantity_or_budget_is_disclosed():
    for header, value in [("<th>单价(元)</th><th>数量</th>", "<td>10</td><td>2</td>"),
                          ("<th>预算总价(元)</th>", "<td>20</td>")]:
        b = build(BASE + table("甲公司") + goods(header, value), candidates("甲公司") + [reference()])
        assert not any(f.kind == "amount" and f.fields["amount_kind"] == "product_line_total" for f in b.facts)


def test_two_candidate_references_to_one_physical_product_do_not_duplicate_line_total():
    cs = candidates("甲公司") + [reference(), Candidate("d", "copy", "t20260101_1", "s", "$[0].detail_entities[0]", "product_reference", {"product_name":"设备甲", "brand":"品牌甲"})]
    b = build(BASE + table("甲公司") + goods("<th>总价(元)</th>", "<td>20</td>"), cs)
    lines = [f for f in b.facts if f.kind=="amount" and f.fields["amount_kind"]=="product_line_total"]
    assert len(lines)==1 and lines[0].fields["decimal_value"]=="20"
    assert set(lines[0].candidate_ids)=={"prod", "copy"}


def test_rowspan_package_keeps_two_distinct_bidders_and_one_physical_package_origin():
    body = BASE + ('<p>评审信息</p><table><tr><th>采购包</th><th>投标人名称</th><th>综合得分</th></tr>'
                   '<tr><td rowspan="2">合同包1</td><td>甲公司</td><td>90</td></tr>'
                   '<tr><td>乙公司</td><td>80</td></tr></table>')
    b = build(body, candidates("", bidders=["甲公司", "乙公司"]))
    participation = [f for f in b.facts if f.kind == "participation"]
    assert len(participation) == 2
    assert len({f.fields["subject_id"] for f in participation}) == 2
    assert len({f.fields["scope_id"] for f in participation}) == 1
    scopes = [f for f in b.facts if f.kind == "event" and f.fields["lot_raw"] == "合同包1"]
    assert len(scopes) == 1
    evidence = [e for e in b.evidence if e.evidence_id in scopes[0].evidence_ids]
    assert {(e.locator.table, e.locator.row, e.locator.cell) for e in evidence} == {(0, 1, 0)}


def test_shared_amount_cell_cannot_duplicate_an_award_total_for_two_suppliers():
    body = BASE + ('<p>合同包1</p><table><tr><th>供应商名称</th><th>中标金额</th></tr>'
                   '<tr><td>甲公司</td><td rowspan="2">100元</td></tr><tr><td>乙公司</td></tr></table>')
    b = build(body, candidates("甲公司；乙公司"))
    assert not any(f.kind == "amount" and f.fields["amount_kind"] == "award_total" and f.validation_status == "verified" for f in b.facts)


@__import__('pytest').mark.private_data
def test_real_manufacturer_embedded_in_spec_is_bound_by_explicit_lot_name():
    root = Path(__file__).resolve().parents[2] / "task1-release-validation-20261012-v12/stage-final-r2/backend/sample_data"
    html = (root / "t20260206_26158480.html").read_bytes()
    cs = [Candidate("d", "prod", "t20260206_26158480", "s", "$[0].entities[0]", "product_reference", {"product_name": "3标段鲜鸡蛋", "brand": "/"})]
    index = index_html("t20260206_26158480", html, "d")
    b = verify_relations(cs, index, resolve_identities(cs, index, "d"))
    supply = next(f for f in b.facts if f.kind == "supply" and f.fields["provider_kind"] == "product_supplier")
    provider = next(f for f in b.facts if f.record_id == supply.fields["provider_organization_id"])
    assert provider.fields["raw_name"] == "疏附县霖鑫家庭农场"
    event = next(f for f in b.facts if f.record_id == supply.fields["scope_id"])
    assert event.fields["lot_raw"] == "3标段"


def test_explicit_source_manufacturer_is_not_lost_when_model_omitted_brand_candidate():
    body=BASE+table("甲公司")+goods("<th>生产厂家</th>","<td>乙制造有限公司</td>")
    b=build(body,candidates("甲公司"))
    suppliers=[f for f in b.facts if f.kind=="supply" and f.fields["provider_kind"]=="product_supplier"]
    assert len(suppliers)==1 and suppliers[0].fields["product_candidate_id"] is None
    assert suppliers[0].candidate_ids==[] and suppliers[0].evidence_ids
    assert suppliers[0].aggregation_eligibility!="eligible"
