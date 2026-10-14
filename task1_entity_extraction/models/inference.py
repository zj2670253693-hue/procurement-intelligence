"""Optional inference protocol, independent of platform SQL and credentials."""
from dataclasses import dataclass
from decimal import Decimal
import json


class InferenceHalt(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class InferenceRequest:
    task_id: str
    announcement_id: str
    layer: str
    source_file: str
    chunk_index: int
    provider: str
    model: str
    parameters: dict
    messages: tuple[dict, ...]


@dataclass(frozen=True)
class InferenceResponse:
    content: str
    usage: dict | None
    provider_request_id: str | None
    reported_model: str | None


def precise_json_loads(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate_model_json_key")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("non_finite_model_json")

    try:
        return json.loads(text, parse_float=Decimal, parse_constant=constant, object_pairs_hook=pairs)
    except json.JSONDecodeError as exc:
        raise ValueError("invalid_model_json") from exc
