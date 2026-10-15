"""Read-only acceptance checklist, deliberately incapable of manufacturing a pass."""
import hashlib
import importlib.util
from pathlib import Path


def prepare_acceptance(release, target_config):
    module_path = Path(__file__).with_name("preflight.py")
    spec = importlib.util.spec_from_file_location("task3_deploy_preflight", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    root = Path(release)
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise ValueError("real_release_directory_required")
    manifest = root/"release-manifest.json"
    if not manifest.is_file() or manifest.is_symlink():
        raise ValueError("release_manifest_required")
    report = module.preflight({**target_config, "release_root": str(root)})
    return {"kind": "acceptance_preparation_only", "passed": False,
            "release_sha256": hashlib.sha256(manifest.read_bytes()).hexdigest(),
            "environment": report, "new_model_calls": 0,
            "missing_gates": ["real_mysql", "public_tls", "three_accounts", "bounded_counter",
                "finite_model_sample_authorization", "real_model_sample", "three_users_processing",
                "five_scene_cold_hot_render", "linux_memory_cgroup", "old_dataset_fingerprint",
                "whole_branch_review", "admin_guarded_cutover"],
            "ready_to_cutover": False}
