from decimal import Decimal, localcontext
import importlib
import pytest


def test_canonical_bytes_and_ids():
    api = importlib.import_module("task2_relationship_analysis.serialization")
    assert api.canonical_bytes({"b": 2, "a": Decimal("0.10")}) == b'{"a":"0.1","b":2}\n'
    assert api.canonical_bytes({"中文": "原文"}).decode() == '{"中文":"原文"}\n'
    assert api.canonical_bytes({"b": 2, "a": 1}) == api.canonical_bytes({"a": 1, "b": 2})
    with pytest.raises(TypeError):
        api.canonical_bytes({"x": float("nan")})
    assert api.make_dataset_id({}, {"sha": "a"}, "s", "r") != api.make_dataset_id({}, {"sha": "b"}, "s", "r")
    assert api.make_record_id("d", "organization", ("甲",)) != api.make_record_id("d", "organization", ("甲分公司",))


def test_decimal_does_not_round_with_context():
    api = importlib.import_module("task2_relationship_analysis.serialization")
    with localcontext() as context:
        context.prec = 2
        assert api.canonical_bytes(Decimal("123456789.0100")) == b'"123456789.01"\n'
    with pytest.raises(ValueError):
        api.canonical_bytes(Decimal("NaN"))


def test_contract_states_are_independent():
    c = importlib.import_module("task2_relationship_analysis.contracts")
    fields = {"raw_name": "甲", "exact_key": "甲", "identity_basis": "exact_name", "alias_evidence_ids": []}
    f = c.Fact("d", "f", "organization", ("甲",), fields, "verified", "identity_unresolved", "identity_unresolved", ["proof"], [])
    assert f.validation_status == "verified"
    with pytest.raises(ValueError):
        c.Fact("d", "f", "organization", ("甲",), fields, "verified", "identity_unresolved", "eligible", ["proof"], [])
    with pytest.raises(ValueError):
        c.Fact("d", "f", "invented_kind", (), {})


def test_implementation_fingerprint_changes_with_source(local_tmp):
    api = importlib.import_module("task2_relationship_analysis.serialization")
    p = local_tmp / "builder.py"
    p.write_text("a = 1\n")
    first = api.implementation_fingerprint(local_tmp)
    p.write_text("a = 2\n")
    assert api.implementation_fingerprint(local_tmp) != first
