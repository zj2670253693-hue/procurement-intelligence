"""Cosmetic, unique name linkage for metadata only, never money conversion."""
import re


def cosmetic_name_key(value):
    text = "" if value is None else str(value)
    return re.sub(r"\s+", "", text.translate(str.maketrans({"（": "(", "）": ")"})))


def select_name_metadata(identity, bindings, raw_counts, awarded_packages):
    key = cosmetic_name_key(identity.get("product_name"))
    if not key or raw_counts[key] != 1:
        return {}
    def original_name(binding):
        proof = binding.get("source_identity")
        # Weak bindings still participate in collision vetoes, never eligibility.
        return proof.get("complete_name", binding["product_name"]) if isinstance(proof, dict) else binding["product_name"]

    matches = [b for b in bindings if cosmetic_name_key(original_name(b)) == key]
    if len(matches) != 1:
        return {}  # Count all bindings, including weak/unproven collisions.
    source = matches[0]
    proof = source.get("source_identity") or {}
    if (not isinstance(proof, dict) or not proof.get("complete_name")
            or proof.get("proved") is not True or type(proof.get("source_table")) is not int
            or type(proof.get("source_row")) is not int or proof["source_table"] < 0 or proof["source_row"] < 1):
        return {}
    for field in ("brand", "spec_model"):
        raw_value, source_value = identity.get(field, ""), proof.get(field, "")
        if raw_value and source_value and raw_value != source_value:
            return {}
    package_key = source.get("package_key", "")
    # Proven source identity is usable even when package ownership stays unknown.
    package_key = package_key if package_key in awarded_packages else ""
    return {"product_name": proof["complete_name"], "package_key": package_key,
            "evidence": source.get("evidence", ""), "source_identity": proof}
