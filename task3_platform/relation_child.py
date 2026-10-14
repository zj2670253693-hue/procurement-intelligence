"""Task2 OfflineBoundary never runs inside API or long-lived worker process."""
from pathlib import Path
import sys
from .contracts import PlatformError, canonical_bytes, strict_json_loads
from .uploads import _hash_file


def main():
    request = strict_json_loads(Path(sys.argv[1]).read_bytes())
    from task2_relationship_analysis.builder import build_dataset
    from task2_relationship_analysis.publication import verify_artifacts
    import task2_relationship_analysis.publication as publication
    original = publication._write_file
    used = 0
    def bounded(path, raw):
        nonlocal used
        used += len(raw)
        if used > request["max_output_bytes"]:
            raise PlatformError("relation_output_limit", 413)
        return original(path, raw)
    publication._write_file = bounded
    raw = Path(request["raw_path"])
    result = build_dataset(Path(request["html_root"]), raw, raw, Path(request["output_root"]), input_profile=request["profile"])
    verify_artifacts(Path(result.output_dir),input_paths={"html_root":request["html_root"],"legacy_path":str(raw),"v11_path":str(raw)})
    response = {"dataset_id": result.dataset_id, "manifest_sha256": _hash_file(Path(result.output_dir) / "manifest.json"), "new_model_calls": 0}
    Path(request["response"]).write_bytes(canonical_bytes(response))


if __name__ == "__main__":
    main()
