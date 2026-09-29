"""
任务三接口：数据集上传与自动化处理

赛题要求「支持批量上传同规范标准的数据集进行自动化处理」，且评审方会
自行上传数据测试，因此这里提供：

  POST /api/upload            批量上传（HTML + 同名 zip 附件，或整包 zip）
  GET  /api/tasks             任务列表
  GET  /api/tasks/{task_id}   进度轮询

上传后立即返回 task_id，实际提取在后台线程执行，避免 HTTP 请求超时。
"""
import io
import shutil
import zipfile
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, UploadFile

import config
from api.services.task_manager import task_manager

router = APIRouter(prefix="/api", tags=["任务三：可视化分析检索平台"])

UPLOAD_ROOT = config.OUTPUT_DIR / "uploads"
ALLOWED_SUFFIXES = {".html", ".htm", ".zip"}


def _zip_contains_html(data: bytes) -> bool:
    """判断这个 zip 是「数据集整包」（内含 html）还是「单篇公告的附件包」"""
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            return any(Path(n).suffix.lower() in (".html", ".htm") for n in zf.namelist())
    except zipfile.BadZipFile:
        return False


def _run_task(task_id: str, work_dir: Path, run_extraction: bool):
    """后台任务：解析上传的数据集并提取标的物"""
    try:
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

        task_manager.update(task_id, status="processing", message="正在解析与提取")

        pipeline = EntityExtractionPipeline(extract_extra=True, max_workers=8)

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
    try:
        for uf in files:
            # 只取文件名部分，防止路径穿越
            name = Path(uf.filename or "").name
            if not name:
                continue
            raw = await uf.read()
            suffix = Path(name).suffix.lower()

            # 数据集整包：解出来平铺到工作目录，保证 html 与同名 zip 相邻
            if suffix == ".zip" and _zip_contains_html(raw):
                with zipfile.ZipFile(io.BytesIO(raw)) as zf:
                    for entry in zf.infolist():
                        if entry.is_dir():
                            continue
                        entry_name = Path(entry.filename).name
                        if Path(entry_name).suffix.lower() not in ALLOWED_SUFFIXES:
                            continue
                        (work_dir / entry_name).write_bytes(zf.read(entry.filename))
                        saved += 1
                continue

            if suffix not in ALLOWED_SUFFIXES:
                skipped.append(name)
                continue

            (work_dir / name).write_bytes(raw)
            saved += 1

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