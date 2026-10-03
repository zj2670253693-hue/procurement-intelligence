"""
任务三接口：数据集上传与自动化处理

赛题要求「支持批量上传同规范标准的数据集进行自动化处理」，且评审方会
自行上传数据测试，因此这里提供：

  POST /api/upload            批量上传（HTML + 同名 zip 附件，或整包 zip）
  GET  /api/tasks             任务列表
  GET  /api/tasks/{task_id}   进度轮询

上传后立即返回 task_id，实际提取在后台线程执行，避免 HTTP 请求超时。
"""
import shutil
import zipfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile

import config
from api.services.task_manager import task_manager

router = APIRouter(prefix="/api", tags=["任务三：可视化分析检索平台"])

UPLOAD_ROOT = config.OUTPUT_DIR / "uploads"
ALLOWED_SUFFIXES = {".html", ".htm", ".zip"}


def _classify_zip(path: Path) -> str:
    """
    区分数据集外层压缩包和单篇公告附件包。

    官方数据分成两包：HTML 包内全是 html，附件包内全是以公告 ID
    命名的 zip。单篇附件包通常直接包含 pdf/docx 等文件。
    """
    try:
        with zipfile.ZipFile(path) as zf:
            files = [i for i in zf.infolist() if not i.is_dir()]
            if any(Path(i.filename).suffix.lower() in (".html", ".htm") for i in files):
                return "dataset"
            nested_zip_count = sum(Path(i.filename).suffix.lower() == ".zip" for i in files)
            if len(files) >= 2 and nested_zip_count >= 2 and nested_zip_count / len(files) >= 0.8:
                return "dataset"
            return "attachment"
    except zipfile.BadZipFile:
        raise ValueError(f"ZIP 文件损坏或格式不正确: {path.name}")


def _extract_dataset_archive(archive_path: Path, work_dir: Path) -> int:
    """安全地把官方数据集外层包平铺到任务目录，不覆盖同名文件。"""
    saved = 0
    with zipfile.ZipFile(archive_path) as zf:
        for entry in zf.infolist():
            if entry.is_dir():
                continue
            # 同时兼容 ZIP 内的 / 与 \，并通过只取 basename 阻断路径穿越。
            entry_name = entry.filename.replace("\\", "/").rsplit("/", 1)[-1]
            if not entry_name or Path(entry_name).suffix.lower() not in ALLOWED_SUFFIXES:
                continue
            target = work_dir / entry_name
            if target.exists():
                raise ValueError(f"压缩包内出现重复文件名，拒绝覆盖: {entry_name}")
            with zf.open(entry) as source, target.open("xb") as destination:
                shutil.copyfileobj(source, destination, length=1024 * 1024)
            saved += 1
    return saved


async def _save_upload(upload: UploadFile, target: Path) -> None:
    """分块保存上传文件，避免把数 GB 的官方附件包一次性读入内存。"""
    with target.open("xb") as output:
        while chunk := await upload.read(1024 * 1024):
            output.write(chunk)


def _run_task(task_id: str, work_dir: Path, run_extraction: bool):
    """后台任务：解析上传的数据集并提取标的物"""
    try:
        task_manager.update(task_id, status="processing", message="正在解析上传数据")
        if not run_extraction:
            # 低成本模式：只解析不调用大模型，用于检查数据质量
            from data_loader.html_parser import HTMLAnnouncementParser

            parser = HTMLAnnouncementParser()
            files = sorted(work_dir.glob("*.html")) + sorted(work_dir.glob("*.htm"))
            ok = 0
            for i, f in enumerate(files, 1):
                try:
                    parser.parse_file(str(f))
                    ok += 1
                except Exception:
                    pass
                task_manager.update(task_id, done=i, success=ok)
            task_manager.update(
                task_id, status="done",
                message="仅解析完成（未调用大模型）",
                done=len(files), success=ok,
            )
            return

        from pipeline import EntityExtractionPipeline
        from output.writer import ResultWriter

        task_manager.update(task_id, message="正在解析与提取")

        pipeline = EntityExtractionPipeline(
            extract_extra=True,
            max_workers=config.PROCESS_MAX_WORKERS,
        )

        def on_progress(done, total, success_count, result):
            task_manager.update(task_id, done=done, success=success_count)
            if result.entities:
                task_manager.add_entities(task_id, len(result.entities))

        results = pipeline.process_directory(str(work_dir), progress_callback=on_progress)

        if pipeline.db:
            pipeline.db.close()

        # 结果单独落盘，不覆盖基准数据集的输出文件
        out_name = f"upload_{task_id}"
        writer = ResultWriter()
        writer.write(results, filename=out_name, fmt="csv")
        writer.write_raw_results(results, filename=f"{out_name}_raw.json")

        ok = sum(1 for r in results if r.success)
        entities = sum(len(r.entities) for r in results)
        task_manager.update(
            task_id, status="done", message="处理完成",
            done=len(results), success=ok, entities=entities,
            result_file=str(config.OUTPUT_DIR / f"{out_name}.csv"),
        )

    except Exception as e:
        task_manager.update(task_id, status="failed", message=f"处理失败：{e}")


@router.post("/upload", summary="批量上传数据集并自动处理")
async def upload_dataset(
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(..., description="HTML 文件与同名 zip 附件，或一个数据集整包 zip"),
    run_extraction: bool = Form(True, description="是否调用大模型提取（false 则仅解析）"),
):
    """
    接收数据集并启动后台处理

    支持两种上传方式：
      1. 直接多选若干 .html 与同名 .zip 附件
      2. 上传一个内含 html 与 zip 的数据集整包 .zip
    """
    if not files:
        raise HTTPException(status_code=400, detail="未收到任何文件")

    task_id = task_manager.create(
        name=files[0].filename or "数据集",
        total_files=len(files),
        run_extraction=run_extraction,
    )
    work_dir = UPLOAD_ROOT / task_id
    work_dir.mkdir(parents=True, exist_ok=True)

    skipped, saved = [], 0
    incoming_dir = work_dir / "_incoming"
    incoming_dir.mkdir(exist_ok=True)
    try:
        for index, uf in enumerate(files):
            # 只取文件名部分，防止路径穿越
            name = Path(uf.filename or "").name
            if not name:
                continue
            suffix = Path(name).suffix.lower()

            if suffix not in ALLOWED_SUFFIXES:
                skipped.append(name)
                continue

            # 先流式写入临时文件，再判断 ZIP 类型；临时文件不使用原名，
            # 防止外层包与包内文件同名时覆盖正在读取的文件。
            incoming = incoming_dir / f"{index:04d}.upload"
            await _save_upload(uf, incoming)

            if suffix == ".zip" and _classify_zip(incoming) == "dataset":
                saved += _extract_dataset_archive(incoming, work_dir)
                incoming.unlink()
                continue

            target = work_dir / name
            if target.exists():
                raise ValueError(f"上传内容中出现重复文件名，拒绝覆盖: {name}")
            incoming.replace(target)
            saved += 1

        shutil.rmtree(incoming_dir, ignore_errors=True)

        html_count = len(list(work_dir.glob("*.html"))) + len(list(work_dir.glob("*.htm")))
        if html_count == 0:
            shutil.rmtree(work_dir, ignore_errors=True)
            task_manager.update(task_id, status="failed", message="未发现任何 HTML 公告文件")
            raise HTTPException(status_code=400, detail="上传内容中未发现 HTML 公告文件")

    except HTTPException:
        raise
    except Exception as e:
        shutil.rmtree(work_dir, ignore_errors=True)
        task_manager.update(task_id, status="failed", message=str(e))
        raise HTTPException(status_code=500, detail=f"保存上传文件失败：{e}")

    task_manager.update(
        task_id, total=html_count,
        message=f"已接收 {saved} 个文件（{html_count} 篇公告）"
                + (f"，跳过 {len(skipped)} 个不支持的文件" if skipped else ""),
    )

    background_tasks.add_task(_run_task, task_id, work_dir, run_extraction)
    return task_manager.get(task_id)


@router.get("/tasks", summary="任务列表")
def list_tasks():
    """返回全部上传处理任务，最新的在前"""
    return {"items": task_manager.list()}


@router.get("/tasks/{task_id}", summary="查询任务进度")
def get_task(task_id: str):
    """供前端定时轮询处理进度"""
    task = task_manager.get(task_id)
    if not task:
        raise HTTPException(status_code=404, detail=f"任务不存在: {task_id}")
    return task
