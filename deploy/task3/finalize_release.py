"""Fail-closed acceptance validator; validation alone never switches a service.

The report is an operator attestation, NOT cryptographic proof that its gates ran.
The actual privileged cutover remains unavailable until cloud evidence is collected.
"""
import math
import re

QUERY_TYPES = {"buyer_suppliers", "buyer_bidders", "supplier_peers", "common_buyers", "common_projects"}
GATES = {"mysql", "tls", "browser", "three_users_processing", "linux_cgroup", "memory_no_oom",
         "model_counter_verified", "finite_model_authorization", "real_model_sample", "whole_branch_review"}


def _number(value, *, minimum=0, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError("invalid_acceptance_metric")
    if value < minimum or maximum is not None and value > maximum:
        raise ValueError("acceptance_metric_outside_limit")
    return value


def finalize_release(acceptance, expected_old_manifest):
    """Pure dry-run. No imports of DB drivers, no filesystem writes or subprocesses."""
    if acceptance.get("kind") != "real_isolated_acceptance":
        raise ValueError("real_isolated_acceptance_required")
    target = acceptance.get("target_database", "")
    if not isinstance(target, str) or len(target)>64 or not re.fullmatch(r"procurement_task3_accept_[A-Za-z0-9_]+", target):
        raise ValueError("isolated_database_required")
    for digest in (acceptance.get("release_sha256"), expected_old_manifest):
        if not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest):
            raise ValueError("acceptance_hash_required")
    if (acceptance.get("old_dataset_fingerprint_unchanged") is not True or
            acceptance.get("old_dataset_fingerprint") != expected_old_manifest):
        raise ValueError("official_dataset_changed")
    gates = acceptance.get("gates", {})
    if any(gates.get(name) is not True for name in GATES):
        raise ValueError("incomplete_acceptance_gates")
    samples = acceptance.get("query_samples", [])
    if not samples or {item.get("query_type") for item in samples} != QUERY_TYPES:
        raise ValueError("five_rendered_query_scenes_required")
    for name in QUERY_TYPES:
        scene = [item for item in samples if item["query_type"] == name]
        for item in scene:
            if not re.fullmatch(r"up_[a-f0-9]{32}", item.get("dataset_id", "")):
                raise ValueError("owned_dataset_render_sample_required")
        elapsed = [_number(item.get("elapsed_ms")) for item in scene]
        if sum(elapsed)/len(elapsed) >= 5000:
            raise ValueError("rendered_query_average_exceeds_5s")
    resource = acceptance.get("resource", {})
    if resource.get("vcpu") != 2:
        raise ValueError("two_vcpu_evidence_required")
    _number(resource.get("memory_bytes"), minimum=3*1024**3, maximum=4*1024**3)
    _number(resource.get("worker_peak_bytes"), maximum=1024**3)
    _number(resource.get("api_peak_bytes"), maximum=768*1024**2)
    _number(resource.get("available_min_bytes"), minimum=512*1024**2)
    if resource.get("oom_events") != 0:
        raise ValueError("oom_detected")
    sample = acceptance.get("model_sample", {})
    for key, maximum in (("announcements", 3), ("sent_calls", 8)):
        if type(sample.get(key)) is not int or not 1 <= sample[key] <= maximum:
            raise ValueError("finite_model_sample_required")
    for key in ("input_tokens", "output_tokens"):
        if type(sample.get(key)) is not int or sample[key] < 0:
            raise ValueError("known_model_usage_required")
    if sample.get("usage_unknown") is not False:
        raise ValueError("unknown_model_usage")
    return {"accepted_attestation_schema": True, "applied": False,
            "old_dataset_fingerprint_unchanged": True,
            "target_database": target, "release_sha256": acceptance["release_sha256"],
            "privileged_cutover_available": False}
