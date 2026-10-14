"""Category prompt rules: announcement-level 品目 vs row-level 品目名称.

The gold follows two different sources depending on the announcement:
  * a 品目 field in the announcement-info table is announcement level, so every
    extracted row carries the same value;
  * a 品目号 / 品目名称 column inside an item table is row level, so each row
    keeps its own value.
The summary prompt has to name both, otherwise the model returns an empty
category even though the value is sitting in the HTML table.
"""

from extractor.prompts import SUMMARY_SYSTEM_PROMPT


def test_prompt_names_the_announcement_level_category_field():
    assert "品目" in SUMMARY_SYSTEM_PROMPT
    assert "公告信息" in SUMMARY_SYSTEM_PROMPT or "公告级" in SUMMARY_SYSTEM_PROMPT


def test_prompt_names_the_row_level_category_column():
    prompt = SUMMARY_SYSTEM_PROMPT

    assert "品目名称" in prompt
    assert "品目号" in prompt


def test_prompt_prefers_row_level_over_announcement_level():
    prompt = SUMMARY_SYSTEM_PROMPT

    assert "优先" in prompt


def test_prompt_forbids_inventing_a_category():
    prompt = SUMMARY_SYSTEM_PROMPT

    assert "自行分类" in prompt or "不要自行" in prompt
