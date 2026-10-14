"""Reserved official dataset adapter: SELECT only, no copied baseline writes."""
from .contracts import PlatformError
from .datasets import Page
from .filters import page_window


class BaselineAdapter:
    def __init__(self, repo, assets):
        manager = getattr(repo, "db", None)
        self.connection = manager._conn if hasattr(manager, "_conn") else repo
        self.assets = assets

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
