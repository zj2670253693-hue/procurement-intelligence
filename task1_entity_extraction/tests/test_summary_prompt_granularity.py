"""Summary prompt must follow the announcement's own granularity.

The human gold for the 40-case validation set follows what the announcement
itself lists: item rows when the announcement lists items, one row per package
when the announcement only gives package headers and defers details to an
attachment. The summary prompt has to say that explicitly, otherwise the model
collapses many-item announcements into a single package row.
"""

from extractor.prompts import SUMMARY_SYSTEM_PROMPT, OFFICIAL_QA_RULE, build_summary_prompt

SAMPLE_TEXT = "某项目中标公告\n供应商名称：虚构供应商甲\n中标（成交）金额：100.0000（万元）"


def test_prompt_requires_one_row_per_listed_item():
    prompt = SUMMARY_SYSTEM_PROMPT

    assert "逐条列出" in prompt or "每条标的物输出一条" in prompt
    assert "标的物" in prompt


def test_prompt_does_not_force_a_single_row_per_package():
    prompt = SUMMARY_SYSTEM_PROMPT

    # The old wording collapsed every announcement into one package row.
    assert "每个采购包只输出一条" not in prompt


def test_prompt_allows_package_row_only_for_deferred_details():
    prompt = SUMMARY_SYSTEM_PROMPT

    assert "详见附件" in prompt
    assert "无法逐条列出" in prompt


def test_prompt_ignores_global_total_amount():
    prompt = SUMMARY_SYSTEM_PROMPT

    assert "项目/采购包" in prompt and "产品行" in prompt


def test_summary_prompt_reports_detail_status():
    system_prompt, user_prompt = build_summary_prompt(
        SAMPLE_TEXT,
        has_tables=False,
        detail_status="missing",
    )

    assert system_prompt == SUMMARY_SYSTEM_PROMPT + OFFICIAL_QA_RULE
    assert "missing" in user_prompt
    assert SAMPLE_TEXT in user_prompt
