import pytest
from fastapi import HTTPException

from api.routers.task1 import (
    detail_entities,
    entity_details_by_announcement,
    entities_by_announcement,
    health,
    stats,
)


class FakeAnnouncements:
    def __init__(self, status="available"):
        self.status = status

    def count_all(self):
        return 2

    def get(self, announcement_id):
        return {
            "announcement_id": announcement_id,
            "detail_status": self.status,
            "detail_error_message": "附件缺失" if self.status == "missing" else "",
        }


class FakeEntities:
    def count_all(self):
        return 1

    def field_fill_stats(self):
        return {
            "product_name": 1,
            "category": 0,
            "brand": 0,
            "spec_model": 0,
            "unit_price": 0,
            "quantity": 0,
            "total_price": 0,
        }

    def list_by_announcement(self, _announcement_id):
        return [{"product_name": "采购包一"}]


class FakeDetails:
    def __init__(self, items=None):
        self.items = items or []
        self.last_search = None

    def count_all(self):
        return len(self.items)

    def list_by_announcement(self, _announcement_id):
        return list(self.items)

    def count_search(self, keyword=""):
        self.last_search = keyword
        return len(self.items)

    def search(self, keyword="", limit=100, offset=0):
        self.last_search = (keyword, limit, offset)
        return list(self.items)[offset:offset + limit]


class FakeRepository:
    def __init__(self, status="available", details=None):
        self.announcements = FakeAnnouncements(status)
        self.entities = FakeEntities()
        self.details = FakeDetails(details)


def test_detail_endpoint_returns_status_and_rows():
    repo = FakeRepository(details=[{"product_name": "设备甲"}])

    response = entity_details_by_announcement("fictional-001", repo=repo)

    assert response == {
        "announcement_id": "fictional-001",
        "detail_status": "available",
        "detail_error_message": "",
        "total": 1,
        "items": [{"product_name": "设备甲"}],
    }


def test_missing_attachment_is_empty_success_not_404():
    response = entity_details_by_announcement(
        "fictional-002",
        repo=FakeRepository(status="missing"),
    )

    assert response["detail_status"] == "missing"
    assert response["items"] == []


def test_unmigrated_detail_status_returns_409():
    with pytest.raises(HTTPException) as caught:
        entity_details_by_announcement(
            "fictional-003",
            repo=FakeRepository(status=None),
        )

    assert caught.value.status_code == 409


def test_stats_and_health_keep_summary_count_and_add_detail_count():
    repo = FakeRepository(details=[{"product_name": "设备甲"}, {"product_name": "设备乙"}])

    assert stats(repo=repo)["entities"] == 1
    assert stats(repo=repo)["detail_entities"] == 2
    assert health(repo=repo)["detail_entities"] == 2


def test_detail_search_paginates_and_old_summary_endpoint_is_unchanged():
    repo = FakeRepository(details=[{"product_name": "设备甲"}])

    response = detail_entities(keyword="设备甲", page=1, page_size=20, repo=repo)
    old_response = entities_by_announcement("fictional-004", repo=repo)

    assert response["total"] == 1
    assert response["items"] == [{"product_name": "设备甲"}]
    assert repo.details.last_search == ("设备甲", 20, 0)
    assert old_response == {
        "announcement_id": "fictional-004",
        "total": 1,
        "items": [{"product_name": "采购包一"}],
    }
