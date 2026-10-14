"""Task paths are derived from validated IDs, never client absolute paths."""
from dataclasses import dataclass
from pathlib import Path
import os
from .contracts import PlatformError, validate_task_id


def reject_link_ancestors(path: Path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise PlatformError("unsafe_runtime_path")
    return path


@dataclass(frozen=True)
class TaskPaths:
    received: Path
    inputs: Path
    extraction: Path
    exports: Path


class RuntimePaths:
    def __init__(self, root: Path):
        if not Path(root).is_absolute() or ".." in Path(root).parts:
            raise PlatformError("unsafe_runtime_path")
        self.root = reject_link_ancestors(Path(root))
        if self.root == Path(self.root.anchor):
            raise PlatformError("unsafe_runtime_path")

    def task(self, task_id: str) -> TaskPaths:
        validate_task_id(task_id)
        reject_link_ancestors(self.root)
        base = self.root / "uploads" / task_id
        result = TaskPaths(*(reject_link_ancestors(base / name) for name in
                             ("received", "inputs", "extraction", "exports")))
        return result
