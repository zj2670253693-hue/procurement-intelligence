"""Reserved official dataset adapter: SELECT only, no copied baseline writes."""
from .contracts import PlatformError
from .datasets import Page
from .filters import page_window
from .contracts import validate_announcement_id,strict_json_loads
from .paths import reject_link_ancestors
from .uploads import _hash_file
from pathlib import Path


class BaselineAdapter:
    def __init__(self, repo, assets):
        manager = getattr(repo, "db", None)
        self.connection = manager._conn if hasattr(manager, "_conn") else repo
        self.assets = assets
        self.repo=repo if hasattr(repo,"announcements") else None

    def search(self, actor, dataset_id, filters, page=1, page_size=20):
        offset = page_window(page, page_size)
        if dataset_id != "official":
            raise PlatformError("invalid_baseline_scope")
        with self.connection.cursor() as cursor:
            cursor.execute("SELECT * FROM extracted_entities ORDER BY id")
            rows = cursor.fetchall()
        matches = [row for row in rows if filters.matches(row)]
        items = [{**row, "upload_dataset_id": "official"} for row in matches[offset:offset + page_size]]
        return Page(items, len(matches), page, page_size, "official", "completed")

    def _version(self):
        marker=reject_link_ancestors(Path(self.assets)/"task2-version.txt")
        if not marker.is_file(): raise PlatformError("official_version_not_ready",503)
        version=marker.read_text(encoding="utf-8").strip()
        from .relations import ScopedRepository
        from task2_query_service.storage import MySQLRepository
        repository=MySQLRepository(self.connection,artifact_root=Path(self.assets)/"task2")
        repository.resolve_version(version)
        return ScopedRepository(repository,version)

    def relations(self): return self._version()

    def source_path(self,aid):
        validate_announcement_id(aid)
        if self.repo is None: raise PlatformError("official_read_adapter_required",503)
        from api.routers.task1 import _resolve_source
        ann=self.repo.announcements.get(aid)
        if not ann: raise PlatformError("announcement_not_found",404)
        path=_resolve_source(ann.get("file_path") or "")
        if path is None: raise PlatformError("official_source_unavailable",404)
        path=reject_link_ancestors(path)
        relation=self._version()
        manifest,_=relation.metadata(relation.resolve_version())
        expected=manifest["input_manifest"]["html"].get(aid)
        if not expected or _hash_file(path)!=expected["sha256"]: raise PlatformError("official_source_integrity",409)
        return path

    def announcement(self,actor,dataset_id,aid):
        if dataset_id!="official": raise PlatformError("invalid_baseline_scope")
        path=self.source_path(aid)
        import zipfile
        attachments=[]
        archive=path.with_suffix(".zip")
        if archive.is_file():
            reject_link_ancestors(archive)
            with zipfile.ZipFile(archive) as source: attachments=[item.filename for item in source.infolist() if not item.is_dir()]
        return {"upload_dataset_id":"official","announcement_id":aid,"html_text":path.read_bytes().decode("utf-8-sig"),"source_sha256":_hash_file(path),"attachments":attachments,"availability":"completed"}

    def attachments(self,aid):
        info=self.announcement(None,"official",aid)
        return {"upload_dataset_id":"official","announcement_id":aid,"total":len(info["attachments"]),"items":[{"name":name,"text":"","length":0} for name in info["attachments"]],"message":"使用原始附件下载查看；新接口不在API请求中重复OCR。"}

    def details(self,aid,page=1,page_size=200):
        validate_announcement_id(aid)
        offset=page_window(page,page_size)
        if self.repo is None: raise PlatformError("official_read_adapter_required",503)
        ann=self.repo.announcements.get(aid)
        if not ann: raise PlatformError("announcement_not_found",404)
        items=self.repo.details.list_by_announcement(aid)
        return {"upload_dataset_id":"official","announcement_id":aid,"items":items[offset:offset+page_size],"total":len(items),"detail_status":ann.get("detail_status"),"detail_error_message":ann.get("detail_error_message") or ""}

    def stats(self):
        if self.repo is None: raise PlatformError("official_read_adapter_required",503)
        from api.routers.task1 import stats
        return {**stats(repo=self.repo),"upload_dataset_id":"official"}

    def workbook(self):
        path=reject_link_ancestors(Path(self.assets)/"task1-standard.xlsx")
        manifest=reject_link_ancestors(Path(self.assets)/"task1-standard-manifest.json")
        if not path.is_file() or not manifest.is_file(): raise PlatformError("official_export_unavailable",503)
        if _hash_file(path)!=strict_json_loads(manifest.read_bytes())["sha256"]: raise PlatformError("official_export_integrity",409)
        return path
