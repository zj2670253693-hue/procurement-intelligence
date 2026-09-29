"""
数据集上传处理的任务管理

上传接口不能同步跑提取（1038 篇要十几分钟，HTTP 必然超时），
因此采用「上传即返回 task_id + 后台处理 + 前端轮询进度」的模式。

任务状态保存在内存中，单进程部署足够；若将来多 worker 部署，
把这里换成 Redis / 数据库即可，接口形态不变。
"""
import threading
import uuid
from datetime import datetime
from typing import Dict, List, Optional


class TaskManager:
    """内存态任务注册表（线程安全）"""

    def __init__(self):
        self._tasks: Dict[str, Dict] = {}
        self._lock = threading.Lock()

    def create(self, name: str, total_files: int, run_extraction: bool) -> str:
        task_id = uuid.uuid4().hex[:12]
        now = datetime.now().isoformat(timespec="seconds")
        with self._lock:
            self._tasks[task_id] = {
                "task_id": task_id,
                "name": name,
                "status": "pending",      # pending / processing / done / failed
                "run_extraction": run_extraction,
                "total": total_files,
                "done": 0,
                "success": 0,
                "entities": 0,
                "message": "任务已创建，等待处理",
                "result_file": "",
                "created_at": now,
                "updated_at": now,
            }
        return task_id

    def update(self, task_id: str, **fields):
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return
            task.update(fields)
            task["updated_at"] = datetime.now().isoformat(timespec="seconds")

    def add_entities(self, task_id: str, n: int):
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task["entities"] += n
                task["updated_at"] = datetime.now().isoformat(timespec="seconds")

    def get(self, task_id: str) -> Optional[Dict]:
        with self._lock:
            task = self._tasks.get(task_id)
            return dict(task) if task else None

    def list(self) -> List[Dict]:
        with self._lock:
            return sorted(
                (dict(t) for t in self._tasks.values()),
                key=lambda t: t["created_at"],
                reverse=True,
            )


# 全局单例，供路由层复用
task_manager = TaskManager()