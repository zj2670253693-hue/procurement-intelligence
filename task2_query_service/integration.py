"""Deployment composition. No schema initialization or imports on API startup."""
import hashlib
import json
import os
import re
from functools import lru_cache
from pathlib import Path
from fastapi import HTTPException
from fastapi.responses import FileResponse
from .api import create_router
from .storage import MySQLRepository


def install(app, backend_root):
    root=Path(backend_root)
    def repository():
        from database.connection import DatabaseManager
        db=DatabaseManager().connect(create_db=False,verbose=False)
        try:
            repo=MySQLRepository(db._conn,cache_namespace=(db.config.host,db.config.port,db.config.database),
                                 artifact_root=root/'release_assets/task2')
            pinned_id=os.getenv('TASK2_DATASET_ID')
            if not pinned_id:
                marker=root/'release_assets'/'task2-version.txt'
                pinned_id=marker.read_text(encoding='utf-8').strip() if marker.exists() else None
            original=repo.resolve_version
            repo.resolve_version=lambda requested=None: original(requested or pinned_id)
            yield repo
        finally: db.close()
    app.include_router(create_router(repository))

    @lru_cache(maxsize=2)
    def read_json(name):
        path=root/'release_assets'/name
        if not path.is_file(): raise HTTPException(503,'标准展示资源尚未发布')
        return json.loads(path.read_text(encoding='utf-8'))

    def require_aid(aid):
        if not re.fullmatch(r't\d{8}_\d+',aid): raise HTTPException(400,'公告编号格式不正确')

    @app.get('/api/task1/standard-items/{announcement_id}',tags=['任务一标准展示'])
    def standard_items(announcement_id: str):
        require_aid(announcement_id)
        table=read_json('task1-tables.json')
        items=[r for r in table['tables']['标的表'] if r['原公告ID']==announcement_id]
        return dict(announcement_id=announcement_id,headers=table['headers']['标的表'],items=items,
                    total=len(items),version='v14-price-projection',database_written=False,
                    amount_unit='万元',raw_fields_preserved=True)

    @app.get('/api/task1/official-notes/{announcement_id}',tags=['任务一分类备注'])
    def official_notes(announcement_id: str):
        require_aid(announcement_id)
        index=read_json('task1-notes.json')
        if announcement_id not in index: raise HTTPException(404,'未找到该公告的分类说明')
        return dict(announcement_id=announcement_id,notes=index[announcement_id],
                    note='预算、需求、入围及附件候选明细不是已发生交易，不自动进入基础评分。')

    @app.get('/api/task1/export/standard',tags=['任务一标准导出'])
    def standard_export():
        asset=root/'release_assets'/'task1-standard.xlsx'
        manifest=root/'release_assets'/'task1-standard-manifest.json'
        if not asset.is_file() or not manifest.is_file():
            raise HTTPException(503,'标准三表尚未发布')
        expected=json.loads(manifest.read_text(encoding='utf-8'))['sha256']
        if hashlib.sha256(asset.read_bytes()).hexdigest()!=expected:
            raise HTTPException(503,'标准文件校验失败，停止下载')
        return FileResponse(asset,filename='任务一-标准三表.xlsx',
                            media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
