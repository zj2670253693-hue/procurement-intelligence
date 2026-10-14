import json
from pathlib import Path
import pytest


@pytest.fixture
def local_tmp(request):
    # Inherit readable parent ACL; never reuse pytest's auto-cleaned directories.
    import uuid
    root = Path(__file__).resolve().parents[1] / "test-artifacts"
    root.mkdir(exist_ok=True)
    path = root / ((request.node.originalname or "case")[:12] + "-" + uuid.uuid4().hex[:16])
    path.mkdir()
    return path


@pytest.fixture
def input_case(local_tmp):
    def build(extra=None, body="<p>项目名称：测试项目</p>", aid="t20260101_1"):
        base = local_tmp / str(len(list(local_tmp.iterdir())))
        base.mkdir()
        html = base / "html"
        html.mkdir()
        (html / (aid + ".html")).write_text(body, encoding="utf-8")
        legacy = base / "legacy.json"
        v11 = base / "v11.json"
        legacy.write_text(json.dumps([{"announcement_id": aid, "success": True, "error": "", "entities": [], "extra_info": extra or {}}], ensure_ascii=False), encoding="utf-8")
        v11.write_text(json.dumps([{"announcement_id": aid, "entities": [], "detail_entities": [], "extra_info": {}}]), encoding="utf-8")
        return html, legacy, v11
    return build
