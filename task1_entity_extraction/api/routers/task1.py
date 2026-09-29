"""
任务一接口：标的物检索与统计

对应赛题「任务一：实体识别与自动化提取」，
提供标的物全字段检索、按公告查看、字段填充率统计等能力，
供任务三可视化平台调用。
"""
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query

import config
from api.deps import get_repository
from database.dao import DataRepository

router = APIRouter(prefix="/api", tags=["任务一：实体识别与自动化提取"])


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