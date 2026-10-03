"""
任务一接口：标的物检索与统计

对应赛题「任务一：实体识别与自动化提取」，
提供标的物全字段检索、按公告查看、字段填充率统计等能力，
供任务三可视化平台调用。
"""
from pathlib import Path
from typing import Any, Dict, List
import zipfile

from fastapi import APIRouter, Depends, HTTPException, Query

import config
from api.deps import get_repository
from data_loader.attachment_parser import AttachmentParser
from data_loader.html_parser import HTMLAnnouncementParser
from database.dao import DataRepository

router = APIRouter(prefix="/api", tags=["任务一：实体识别与自动化提取"])

# 项目根目录（task1_entity_extraction），用于解析相对的源文件路径
PROJECT_DIR = Path(__file__).resolve().parents[2]


def _resolve_source(file_path: str) -> Path | None:
    """
    把数据库里的 file_path 解析成真实存在的文件路径

    历史数据里 file_path 有绝对路径也有相对路径（相对于项目目录），
    这里统一处理，找不到就返回 None（此时回退用库里的 raw_text）。
    """
    if not file_path:
        return None
    p = Path(file_path)
    for cand in (p, PROJECT_DIR / file_path):
        try:
            if cand.is_file():
                return cand
        except OSError:
            continue
    return None


@router.get("/health", summary="健康检查")
def health(repo: DataRepository = Depends(get_repository)) -> Dict[str, Any]:
    """检查服务与数据库连通性，并返回数据总量"""
    return {
        "status": "ok",
        "database": "connected",
        "announcements": repo.announcements.count_all(),
        "entities": repo.entities.count_all(),
    }


@router.get("/entities", summary="标的物检索（分页）")
def list_entities(
    keyword: str = Query("", description="关键词，匹配产品名称/品牌/规格型号/品目"),
    field: str = Query("", description="只返回该字段非空的记录，如 unit_price"),
    page: int = Query(1, ge=1, description="页码，从 1 开始"),
    page_size: int = Query(20, ge=1, le=200, description="每页条数"),
    repo: DataRepository = Depends(get_repository),
) -> Dict[str, Any]:
    """按关键词与字段条件检索标的物，返回分页结果"""
    try:
        total = repo.entities.count_search(keyword, field)
        items = repo.entities.search(
            keyword, field, limit=page_size, offset=(page - 1) * page_size
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {"total": total, "page": page, "page_size": page_size, "items": items}


@router.get("/entities/{announcement_id}", summary="查看某篇公告的全部标的物")
def entities_by_announcement(
    announcement_id: str,
    repo: DataRepository = Depends(get_repository),
) -> Dict[str, Any]:
    """按公告 ID 返回该公告提取出的所有标的物"""
    items = repo.entities.list_by_announcement(announcement_id)
    if not items:
        raise HTTPException(status_code=404, detail=f"未找到公告 {announcement_id} 的标的物")

    return {"announcement_id": announcement_id, "total": len(items), "items": items}


@router.get("/announcements/{announcement_id}", summary="查看公告原文")
def get_announcement(
    announcement_id: str,
    repo: DataRepository = Depends(get_repository),
) -> Dict[str, Any]:
    """
    返回公告元信息与 HTML 正文，用于在检索页查看提取来源

    附件正文单独由 /attachments 接口按需加载——附件解析较慢，
    拆开可以让抽屉秒开，用户切到附件页签时再加载。
    """
    ann = repo.announcements.get(announcement_id)
    if not ann:
        raise HTTPException(status_code=404, detail=f"未找到公告 {announcement_id}")

    src = _resolve_source(ann.get("file_path") or "")
    html_text = ""
    attachment_files: List[str] = []

    if src:
        try:
            html_text = HTMLAnnouncementParser().parse_file(str(src)).html_text
        except Exception:
            html_text = ""
        zip_path = src.parent / f"{src.stem}.zip"
        if zip_path.exists():
            try:
                with zipfile.ZipFile(zip_path) as zf:
                    attachment_files = [i.filename for i in zf.infolist() if not i.is_dir()]
            except zipfile.BadZipFile:
                pass

    # 源文件不可用时，回退到数据库里存的原文
    if not html_text:
        html_text = ann.get("raw_text") or ""

    return {
        "announcement_id": announcement_id,
        "title": ann.get("title") or "",
        "status": ann.get("status") or "",
        "error_message": ann.get("error_message") or "",
        "source_exists": src is not None,
        "html_text": html_text,
        "html_length": len(html_text),
        "attachment_files": attachment_files,
    }


@router.get("/announcements/{announcement_id}/attachments", summary="查看公告附件内容")
def get_announcement_attachments(
    announcement_id: str,
    repo: DataRepository = Depends(get_repository),
) -> Dict[str, Any]:
    """按需解析并返回附件文本"""
    ann = repo.announcements.get(announcement_id)
    if not ann:
        raise HTTPException(status_code=404, detail=f"未找到公告 {announcement_id}")

    src = _resolve_source(ann.get("file_path") or "")
    if not src:
        return {"announcement_id": announcement_id, "total": 0, "items": [],
                "message": "源文件已不存在，无法解析附件"}

    zip_path = src.parent / f"{src.stem}.zip"
    if not zip_path.exists():
        return {"announcement_id": announcement_id, "total": 0, "items": [],
                "message": "该公告没有附件包"}

    texts, _tables = AttachmentParser().parse_zip(str(zip_path), announcement_id)
    items = [{"name": name, "text": text, "length": len(text)} for name, text in texts.items()]
    message = "" if items else "附件包内的文件均无法解析出文本（可能为扫描件或加密文件）"
    return {"announcement_id": announcement_id, "total": len(items),
            "items": items, "message": message}


@router.get("/stats", summary="提取结果总览统计")
def stats(repo: DataRepository = Depends(get_repository)) -> Dict[str, Any]:
    """返回公告数、标的物数，以及 7 个核心字段的填充率"""
    total = repo.entities.count_all()
    fill = repo.entities.field_fill_stats()

    return {
        "announcements": repo.announcements.count_all(),
        "entities": total,
        "field_fill": {
            config.FIELD_LABELS.get(name, name): {
                "count": count,
                "rate": round(count / total * 100, 1) if total else 0.0,
            }
            for name, count in fill.items()
        },
    }
