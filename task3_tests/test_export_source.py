"""Guard missing-summary recovery without changing the frozen official parser."""
from copy import deepcopy
import pytest


def original(**updates):
    return {"project_name":"", "procurement_unit":"", "classification":"award_candidate",
            "packages":[], "awards":[], "divided":None, "bidders":[{"name":"原名单"}],
            "item_bindings":[{"product_name":"原标的", "package_key":""}],
            "source_sha256":"original-source-digest", "warnings":[], **updates}


def page(*, title="合成项目成交公告", project="<p>项目名称：合成项目</p>",
         buyer="<h6>采购人信息</h6><p>名称：合成采购人</p><h6>采购代理机构信息</h6><p>名称：合成代理机构</p>",
         before="<p>三、中标（成交）信息</p><p>合同包1</p>",
         table="<table><tr><th>供应商名称</th><th>中标金额</th></tr><tr><td>合成供应商</td><td>100.10元</td></tr></table>"):
    return (f"<html><head><title>{title}</title></head><body>{project}{buyer}{before}{table}</body></html>").encode()


def test_restore_visible_source_metadata_not_agency_or_model(platform_module):
    restore = platform_module("export_source").restore_uploaded_summary
    source = original()
    before = deepcopy(source)
    result = restore(page(), source)
    assert result["project_name"] == "合成项目"
    assert result["procurement_unit"] == "合成采购人"
    assert result["awards"][0]["supplier"] == "合成供应商"
    assert result["awards"][0]["amount"] == "100.10元"
    assert result["awards"][0]["package_key"] == "包:1"
    assert result["source_sha256"] == source["source_sha256"]
    assert result["bidders"] == source["bidders"]
    assert result["item_bindings"] == source["item_bindings"]
    assert source == before, "neither the frozen source nor seven fields may be mutated"


@pytest.mark.parametrize("project", ["", "<p>项目名称：甲项目</p><p>项目名称：乙项目</p>",
    "<p>项目名称：详见附件</p>", "<p hidden>项目名称：隐藏项目</p>",
    "<script>项目名称：脚本项目</script>", '<p style="display:none">项目名称：隐藏项目</p>'])
def test_no_unique_visible_project_means_no_award_recovery(platform_module, project):
    result = platform_module("export_source").restore_uploaded_summary(page(project=project), original())
    assert result["project_name"] == ""
    assert result["awards"] == []


def test_existing_conflicting_project_is_never_overridden(platform_module):
    result = platform_module("export_source").restore_uploaded_summary(page(), original(project_name="另一项目"))
    assert result["project_name"] == "另一项目"
    assert result["awards"] == []
    assert "project_name" in result["uploaded_summary_recovery"]["conflicts"]


def test_conflicting_buyers_do_not_become_an_arbitrary_winner(platform_module):
    result = platform_module("export_source").restore_uploaded_summary(page(
        buyer="<p>采购单位：甲采购人</p><p>采购单位：乙采购人</p>"), original())
    assert result["procurement_unit"] == ""
    assert "procurement_unit" in result["uploaded_summary_recovery"]["conflicts"]


@pytest.mark.parametrize("before", ["<p>三、采购需求</p><p>合同包1</p>",
    "<p>三、中标（成交）信息</p><p>采购需求预算明细</p>",
    "<p>三、中标（成交）信息</p><p>候选人报价</p>"])
def test_demand_budget_and_candidates_are_not_actual_awards(platform_module, before):
    result = platform_module("export_source").restore_uploaded_summary(page(before=before), original())
    assert result["awards"] == []


@pytest.mark.parametrize("title", ["框架协议入围公示", "中标候选人公告", "采购项目废标公告"])
def test_intention_and_nonaward_titles_do_not_restore_transactions(platform_module, title):
    result = platform_module("export_source").restore_uploaded_summary(
        page(title=title, before="<p>入围供应商，尚无实际成交</p><p>三、中标（成交）信息</p><p>合同包1</p>"), original())
    assert result["awards"] == []


@pytest.mark.parametrize("table", [
    '<table hidden><tr><th>供应商名称</th><th>中标金额</th></tr><tr><td>隐藏公司</td><td>10元</td></tr></table>',
    '<table><tr><th>供应商名称</th><th>中标金额</th></tr><tr><td>某公司</td><td>78%</td></tr></table>',
    '<table><tr><th>供应商名称</th><th>中标金额</th></tr><tr><td>某公司</td><td>100</td></tr></table>',
    '<table><tr><th>供应商名称</th><th>中标金额</th></tr><tr><td colspan="2">某公司100元</td></tr></table>',
])
def test_hidden_ambiguous_and_nonexplicit_amounts_are_not_recovered(platform_module, table):
    result = platform_module("export_source").restore_uploaded_summary(page(table=table), original())
    assert result["awards"] == []


def test_existing_awards_are_unchanged_and_not_concatenated(platform_module):
    awards = [{"supplier":"原成交公司", "amount":"123元", "package_key":""}]
    result = platform_module("export_source").restore_uploaded_summary(page(), original(awards=awards))
    assert result["awards"] == awards
    assert result["uploaded_summary_recovery"]["award_rows_restored"] == 0


def test_do_not_claim_known_package_when_source_says_not_divided(platform_module):
    result = platform_module("export_source").restore_uploaded_summary(
        page(before="<p>本项目不分包</p><p>三、中标（成交）信息</p><p>合同包1</p>"), original())
    assert result["awards"] == []
    assert "divided" in result["uploaded_summary_recovery"]["conflicts"]
