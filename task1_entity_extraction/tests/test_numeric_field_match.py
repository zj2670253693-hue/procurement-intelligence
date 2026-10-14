"""Literal numeric contracts: never erase decimals or infer missing units."""

import pytest

from evaluation.metrics import Evaluator


@pytest.mark.parametrize("pred,gold,want", [
    ("1.0台", "10台", False),
    ("1.0000项", "1项", True),
    ("1.00", "1", True),
    ("1", "1台", False),
    ("1台", "1套", False),
    ("1", "1（台/套）", False),
    ("1台", "1（台/套）", False),
    ("１．００项", "1项", True),
    ("1,000.00台", "1000台", True),
    ("1,2台", "12台", False),
    ("1-2台", "12台", False),
    ("1/2台", "12台", False),
    ("1.00人", "1人", True),
    ("1.00年", "1年", True),
    ("1年", "12月", False),
    ("1.00未知单位", "1未知单位", False),
    ("1（台/套）", "1(台/套)", True),
    ("", "", True),
    ("", "0", False),
    ("详见附件", "详见附件", True),
    ("详见附件。", "详见附件", False),
])
def test_quantity_compares_whole_value_and_explicit_unit(pred, gold, want):
    evaluator = Evaluator()
    assert evaluator.field_match(pred, gold, "quantity") is want
    assert evaluator.field_match(gold, pred, "quantity") is want


@pytest.mark.parametrize("field", ["unit_price", "total_price"])
@pytest.mark.parametrize("pred,gold,want", [
    ("1.2万元", "12万元", False),
    ("1万元", "10000元", True),
    ("1亿元", "100000000元", True),
    ("1.00元", "1元", True),
    ("1.00", "1元", True),
    ("￥1,000.00", "1000元", True),
    ("１．００（万元）", "10000元", True),
    ("1万元（人民币）", "人民币10000元", True),
    ("1,2元", "12元", False),
    ("100元/年", "100元", False),
    ("100元/年", "100元/月", False),
    ("1万元/年", "10000元/年", True),
    ("100元／（年）", "100.00元/年", True),
    ("100元每年", "100元/年", True),
    ("100元/台", "100元/套", False),
    ("100元/未知单位", "100元", False),
    ("1万元/未知单位", "10000元/未知单位", False),
    ("100元/人/年", "100元/人", False),
    ("100元和200元", "100元", False),
    ("100-200元", "100200元", False),
    ("78%", "78元", False),
    ("100USD", "100元", False),
    ("100元（含税）", "100元（不含税）", False),
    ("", "", True),
    ("", "0元", False),
    ("附件缺失", "附件缺失", True),
    ("详见附件。", "详见附件", False),
])
def test_amount_preserves_decimal_currency_and_rate_semantics(field, pred, gold, want):
    evaluator = Evaluator()
    assert evaluator.field_match(pred, gold, field) is want
    assert evaluator.field_match(gold, pred, field) is want


@pytest.mark.parametrize("field,pred,gold,want", [
    ("product_name", "核心设备（甲）", "核心设备甲", True),
    ("category", "通用办公设备", "通用办公设备类", True),
    ("brand", "虚构供应商有限公司", "虚构供应商有限公司。", True),
    ("spec_model", "X-1.0", "X10", True),
    ("product_name", "服务器1", "路由器1", False),
])
def test_non_numeric_field_rules_remain_unchanged(field, pred, gold, want):
    assert Evaluator().field_match(pred, gold, field) is want


@pytest.mark.parametrize("field", ["unit_price", "total_price"])
@pytest.mark.parametrize("pred,gold,want", [
    ("100(元/年)", "100元/年", True),
    ("1（万元/年）", "10000元/年", True),
    ("100元（/年）", "100元/年", True),
    ("100 （ 元 / 年 ）", "100.00元/年", True),
    ("1(万元/年)(人民币)", "10000元/年", True),
    ("100(元/年)", "100元/月", False),
    ("100(元/年)", "100元", False),
    ("1（万元/年）", "10000元/月", False),
    ("1（万元/年）", "10000元", False),
    ("100元（/年）", "100元/月", False),
    ("100元（/年）", "100元", False),
    ("100(元/未知单位)", "100元", False),
    ("100(元/人/年)", "100元/人", False),
])
def test_rate_wrappers_change_presentation_not_denominator(field, pred, gold, want):
    evaluator = Evaluator()
    assert evaluator.field_match(pred, gold, field) is want
    assert evaluator.field_match(gold, pred, field) is want
