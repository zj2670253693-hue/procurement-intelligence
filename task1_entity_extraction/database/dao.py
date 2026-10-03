"""
数据访问层（DAO）：封装所有数据库读写操作
"""
from typing import List, Dict, Optional
from database.connection import DatabaseManager
from models.schemas import ExtractionResult, ExtractedEntity, deduplicate_entities


class AnnouncementDAO:
    """公告数据访问"""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def upsert(self, announcement_id: str, title: str = "", file_path: str = "",
               raw_text: str = "", status: str = "done", error_message: str = ""):
        """插入或更新公告记录"""
        sql = """
            INSERT INTO announcements (announcement_id, title, file_path, raw_text, status, error_message)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                title = VALUES(title),
                file_path = VALUES(file_path),
                raw_text = VALUES(raw_text),
                status = VALUES(status),
                error_message = VALUES(error_message)
        """
        with self.db.cursor() as cur:
            cur.execute(sql, (announcement_id, title, file_path, raw_text, status, error_message))

    def get(self, announcement_id: str) -> Optional[Dict]:
        sql = "SELECT * FROM announcements WHERE announcement_id = %s"
        with self.db.cursor() as cur:
            cur.execute(sql, (announcement_id,))
            return cur.fetchone()

    def list_all(self) -> List[Dict]:
        sql = "SELECT * FROM announcements ORDER BY created_at DESC"
        with self.db.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()

    def count_all(self) -> int:
        with self.db.cursor() as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM announcements")
            return cur.fetchone()["cnt"]


class EntityDAO:
    """标的物提取结果数据访问"""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def save_entities(self, announcement_id: str, entities: List[ExtractedEntity]):
        """保存某公告的所有提取结果（先删后插，保证幂等）"""
        entities = deduplicate_entities(entities)
        # 先删除该公告的旧数据
        with self.db.cursor() as cur:
            cur.execute("DELETE FROM extracted_entities WHERE announcement_id = %s", (announcement_id,))

        # 批量插入新数据
        if not entities:
            return
        sql = """
            INSERT INTO extracted_entities
                (announcement_id, product_name, category, brand, spec_model, unit_price, quantity, total_price)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """
        rows = [
            (announcement_id, e.product_name, e.category, e.brand,
             e.spec_model, e.unit_price, e.quantity, e.total_price)
            for e in entities
        ]
        with self.db.cursor() as cur:
            cur.executemany(sql, rows)

    def list_by_announcement(self, announcement_id: str) -> List[Dict]:
        sql = "SELECT * FROM extracted_entities WHERE announcement_id = %s ORDER BY id"
        with self.db.cursor() as cur:
            cur.execute(sql, (announcement_id,))
            return cur.fetchall()

    # 允许作为筛选条件的字段白名单（字段名会拼进 SQL，必须白名单校验防注入）
    FILTERABLE_FIELDS = {
        "product_name", "category", "brand", "spec_model",
        "unit_price", "quantity", "total_price",
    }

    def _build_search_where(self, keyword: str = "", field: str = "") -> tuple:
        """构造检索条件，返回 (where 子句, 参数列表)"""
        where = " WHERE 1=1"
        params = []
        if keyword:
            where += (" AND (product_name LIKE %s OR brand LIKE %s"
                      " OR spec_model LIKE %s OR category LIKE %s)")
            like = f"%{keyword}%"
            params.extend([like, like, like, like])
        if field:
            if field not in self.FILTERABLE_FIELDS:
                raise ValueError(f"不支持的筛选字段: {field}")
            where += f" AND {field} <> ''"
        return where, params

    def search(self, keyword: str = "", field: str = "",
               limit: int = 100, offset: int = 0) -> List[Dict]:
        """搜索标的物（任务三检索用，支持分页）"""
        where, params = self._build_search_where(keyword, field)
        sql = f"SELECT * FROM extracted_entities{where} ORDER BY id LIMIT %s OFFSET %s"
        with self.db.cursor() as cur:
            cur.execute(sql, params + [limit, offset])
            return cur.fetchall()

    def count_search(self, keyword: str = "", field: str = "") -> int:
        """统计符合检索条件的标的物总数（配合分页使用）"""
        where, params = self._build_search_where(keyword, field)
        with self.db.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) AS cnt FROM extracted_entities{where}", params)
            return cur.fetchone()["cnt"]

    def field_fill_stats(self) -> Dict[str, int]:
        """统计 7 个核心字段的填充情况"""
        cols = ["product_name", "category", "brand", "spec_model",
                "unit_price", "quantity", "total_price"]
        sql = "SELECT " + ", ".join(
            f"SUM(CASE WHEN {c} <> '' THEN 1 ELSE 0 END) AS {c}" for c in cols
        ) + " FROM extracted_entities"
        with self.db.cursor() as cur:
            cur.execute(sql)
            row = cur.fetchone()
        return {c: int(row[c] or 0) for c in cols}

    def count_all(self) -> int:
        with self.db.cursor() as cur:
            cur.execute("SELECT COUNT(*) as cnt FROM extracted_entities")
            return cur.fetchone()["cnt"]


class ProjectDAO:
    """项目关系数据访问（任务二）"""

    def __init__(self, db: DatabaseManager):
        self.db = db

    def upsert_project(self, announcement_id: str, project_name: str = "",
                       procurement_unit: str = "", winning_supplier: str = "",
                       project_amount: str = ""):
        sql = """
            INSERT INTO project_relations (announcement_id, project_name, procurement_unit, winning_supplier, project_amount)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                project_name = VALUES(project_name),
                procurement_unit = VALUES(procurement_unit),
                winning_supplier = VALUES(winning_supplier),
                project_amount = VALUES(project_amount)
        """
        with self.db.cursor() as cur:
            cur.execute(sql, (announcement_id, project_name, procurement_unit, winning_supplier, project_amount))

    def save_bidders(self, announcement_id: str, bidders: List[str], winner: str = ""):
        """保存投标人列表"""
        # 先删后插
        with self.db.cursor() as cur:
            cur.execute("DELETE FROM bidders WHERE announcement_id = %s", (announcement_id,))
            for bidder in bidders:
                is_winner = 1 if bidder == winner else 0
                cur.execute(
                    "INSERT INTO bidders (announcement_id, bidder_name, is_winner) VALUES (%s, %s, %s)",
                    (announcement_id, bidder, is_winner)
                )

    def query_by_procurement_unit(self, unit_name: str) -> List[Dict]:
        """查询某采购单位的所有项目（任务二场景1）"""
        sql = """
            SELECT pr.*, e.product_name, e.brand, e.total_price
            FROM project_relations pr
            LEFT JOIN extracted_entities e ON pr.announcement_id = e.announcement_id
            WHERE pr.procurement_unit LIKE %s
            ORDER BY pr.created_at DESC
        """
        with self.db.cursor() as cur:
            cur.execute(sql, (f"%{unit_name}%",))
            return cur.fetchall()

    def query_by_supplier(self, supplier_name: str) -> List[Dict]:
        """查询某供应商中标的所有项目（任务二场景3）"""
        sql = """
            SELECT * FROM project_relations
            WHERE winning_supplier LIKE %s
            ORDER BY created_at DESC
        """
        with self.db.cursor() as cur:
            cur.execute(sql, (f"%{supplier_name}%",))
            return cur.fetchall()


class DataRepository:
    """统一数据仓库入口，封装所有 DAO"""

    def __init__(self, db: DatabaseManager):
        self.db = db
        self.announcements = AnnouncementDAO(db)
        self.entities = EntityDAO(db)
        self.projects = ProjectDAO(db)

    def save_extraction_result(
        self,
        result: ExtractionResult,
        title: str = "",
        raw_text: str = "",
        file_path: str = "",
    ):
        """保存完整的提取结果（公告 + 标的物 + 项目关系）"""
        aid = result.announcement_id

        # 1. 保存公告元数据
        status = "done" if result.success else "failed"
        self.announcements.upsert(
            announcement_id=aid,
            title=title,
            file_path=file_path,
            raw_text=raw_text,
            status=status,
            error_message=result.error_message,
        )

        # 2. 保存提取的标的物
        self.entities.save_entities(aid, result.entities)

        # 3. 保存项目关系（任务二需要的额外信息）
        if result.extra_info:
            info = result.extra_info
            self.projects.upsert_project(
                announcement_id=aid,
                project_name=info.get("project_name", ""),
                procurement_unit=info.get("procurement_unit", ""),
                winning_supplier=info.get("winning_supplier", ""),
                project_amount=info.get("project_amount", ""),
            )
            # 保存投标人
            bidders = info.get("bidders", [])
            winner = info.get("winning_supplier", "")
            if bidders:
                self.projects.save_bidders(aid, bidders, winner)
