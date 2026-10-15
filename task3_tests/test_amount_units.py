"""Uploaded-batch amounts must not be silently rescaled or left unit-less.

standard_export.project() normalises money to 万元 (scaleb -4 for 元). That is fine for
the frozen official baseline, but an uploaded batch exported a bare number with no unit
anywhere in the sheet, so 123,456.78元 appeared as 12.345678 and looked like a 10,000x
error to any reader.
"""
import io
import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools' / 'task1_standard_export'))
sys.path.insert(0, str(ROOT / 'task1_entity_extraction'))

import standard_export  # noqa: E402


def test_money_wan_scales_yuan_to_wan_without_losing_precision():
    assert standard_export.money_wan('123,456.78元') == '12.345678'
    assert standard_export.money_wan('1.23元') == '0.000123'
    assert standard_export.money_wan('123,456.78万元') == '123456.78'
    assert standard_export.money_wan('1亿元') == '10000'


def test_uploaded_export_declares_the_unit_it_used():
    """The sheet must say which unit its numbers are in, for uploads especially."""
    source = (ROOT / 'task3_platform' / 'export_child.py').read_text(encoding='utf-8')
    assert '万元' in source, 'the upload export must state the unit it normalised to'
    assert '本批上传抽取结果' in source
