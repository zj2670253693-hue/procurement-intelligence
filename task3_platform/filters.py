"""Seven-field allowlist, literal LIKE, exact Decimal projections (万元)."""
from dataclasses import dataclass, field
from decimal import Decimal, localcontext
import re
from .contracts import PlatformError

FIELDS = ("product_name", "category", "brand", "spec_model", "unit_price", "quantity", "total_price")
NUMERIC = {"unit_price", "quantity", "total_price"}
UNKNOWN = ("", "无", "未公示", "未公布", "未披露", "未知", "None", "null", "--", "/", "附件缺失", "详见附件", "见附件")
SCALAR = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?"
QUANTITY_UNITS=frozenset(("套","批","台","件","个","项","人天","吨","米","平方米","本","辆","张","份"))


def numeric_projection(raw, field, *, declared_unit=None):
    if not isinstance(raw, str) or len(raw) > 256 or field not in NUMERIC:
        return None, None
    if field == "quantity":
        match = re.fullmatch(r"\s*(" + SCALAR + r")\s*(套|批|台|件|个|项|人天|吨|米|平方米|本|辆|张|份)\s*", raw)
    else:
        match = re.fullmatch(r"\s*(?:人民币\s*)?(" + SCALAR + r")\s*(万元|元)\s*", raw)
    if match:
        number, unit = match.groups()
    elif declared_unit and re.fullmatch(SCALAR, raw.strip()):
        number, unit = raw.strip(), declared_unit
        if field != "quantity" and unit not in {"元", "万元"}:
            return None, None
    else:
        return None, None
    with localcontext() as context:
        context.prec = 100
        value = Decimal(number.replace(",", ""))
        if field != "quantity" and unit == "元":
            value = value / Decimal("10000")
        unit = unit if field == "quantity" else "万元"
    if value < 0 or max(value.adjusted() + 1, 1) > 35 or max(-value.as_tuple().exponent, 0) > 30:
        return None, None
    return value, unit


@dataclass(frozen=True)
class EntityFilters:
    text: dict = field(default_factory=dict)
    missing: dict = field(default_factory=dict)
    ranges: dict = field(default_factory=dict)
    quantity_unit: str | None = None

    def __post_init__(self):
        if any(not isinstance(value,dict) for value in (self.text,self.missing,self.ranges)):
            raise PlatformError("invalid_entity_filter")
        if self.quantity_unit is not None and (not isinstance(self.quantity_unit,str) or self.quantity_unit not in QUANTITY_UNITS):
            raise PlatformError("invalid_quantity_unit")
        if set(self.text) - set(FIELDS) or set(self.missing) - set(FIELDS) or set(self.ranges) - NUMERIC:
            raise PlatformError("invalid_filter_field")
        if any(not isinstance(value, str) or len(value) > 512 for value in self.text.values()):
            raise PlatformError("invalid_filter_text")
        if any(type(value) is not bool for value in self.missing.values()):
            raise PlatformError("invalid_missing_filter")
        for pair in self.ranges.values():
            if not isinstance(pair,(tuple,list)) or len(pair) != 2 or any(value is not None and (not isinstance(value, (Decimal, int)) or isinstance(value, bool)
                     or not Decimal(value).is_finite()) for value in pair):
                raise PlatformError("invalid_range_filter")
            if pair[0] is not None and pair[1] is not None and pair[0] > pair[1]:
                raise PlatformError("invalid_range_filter")
        if "quantity" in self.ranges and not self.quantity_unit:
            raise PlatformError("quantity_unit_required")

    def sql(self, *, prefix=""):
        clauses, params = [], []
        for name, value in self.text.items():
            literal = value.replace("!", "!!").replace("%", "!%").replace("_", "!_")
            clauses.append(f"{prefix}{name} LIKE %s ESCAPE '!'")
            params.append("%" + literal + "%")
        for name, missing in self.missing.items():
            condition = f"({prefix}{name} IS NULL OR TRIM({prefix}{name}) IN (" + ",".join("%s" for _ in UNKNOWN) + "))"
            clauses.append(condition if missing else "NOT " + condition)
            params.extend(UNKNOWN)
        for name, (low, high) in self.ranges.items():
            for value, operator in ((low, ">="), (high, "<=")):
                if value is not None:
                    clauses.append(f"{prefix}{name}_numeric {operator} %s")
                    params.append(Decimal(value))
            if name == "quantity":
                clauses.append(f"{prefix}quantity_unit=%s")
                params.append(self.quantity_unit)
        return clauses, params

    def matches(self, row):
        if any(text.casefold() not in str(row.get(name) or "").casefold() for name, text in self.text.items()):
            return False
        if any((str(row.get(name) or "").strip() in UNKNOWN) != missing for name, missing in self.missing.items()):
            return False
        for name, (low, high) in self.ranges.items():
            value, unit = numeric_projection(str(row.get(name) or ""), name)
            if value is None or (name == "quantity" and unit != self.quantity_unit) or (low is not None and value < low) or (high is not None and value > high):
                return False
        return True


def page_window(page, page_size):
    if type(page) is not int or type(page_size) is not int or page <= 0 or not 1 <= page_size <= 200:
        raise PlatformError("invalid_pagination")
    offset = (page - 1) * page_size
    if offset >= 10000:
        raise PlatformError("pagination_offset_limit")
    return offset
