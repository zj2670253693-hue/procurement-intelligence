"""
MySQL 数据库连接管理
"""
import os
import re
from contextlib import contextmanager
from typing import Optional

import pymysql
from pymysql.cursors import DictCursor

import config
from database.schema import CREATE_DATABASE, ALL_TABLES


class DatabaseConfig:
    """数据库配置"""
    def __init__(
        self,
        host: str = None,
        port: int = None,
        user: str = None,
        password: str = None,
        database: str = None,
        charset: str = "utf8mb4",
    ):
        self.host = host or os.getenv("DB_HOST", "localhost")
        self.port = port or int(os.getenv("DB_PORT", "3306"))
        self.user = user or os.getenv("DB_USER", "root")
        self.password = password or os.getenv("DB_PASSWORD", "")
        self.database = database or os.getenv("DB_NAME", "procurement_kg")
        self.charset = charset

    def to_dict(self, include_database: bool = True) -> dict:
        d = {
            "host": self.host,
            "port": self.port,
            "user": self.user,
            "password": self.password,
            "charset": self.charset,
            "cursorclass": DictCursor,
            "autocommit": True,
        }
        if include_database:
            d["database"] = self.database
        return d


class DatabaseManager:
    """数据库管理器：连接、建表、CRUD"""

    def __init__(self, db_config: DatabaseConfig = None):
        self.config = db_config or DatabaseConfig()
        self._conn = None

    def connect(self, create_db: bool = True, verbose: bool = True) -> "DatabaseManager":
        """
        建立数据库连接，可选自动建库

        Args:
            create_db: 是否自动创建数据库
            verbose: 是否打印连接日志（API 频繁建连时建议关闭）
        """
        # 先连接到 MySQL 服务器（不指定数据库），用于建库
        if create_db:
            try:
                conn = pymysql.connect(**self.config.to_dict(include_database=False))
                with conn.cursor() as cursor:
                    cursor.execute(CREATE_DATABASE.format(db_name=self.config.database))
                conn.close()
                if verbose:
                    print(f"[数据库] 已确保数据库 {self.config.database} 存在")
            except Exception as e:
                if verbose:
                    print(f"[数据库警告] 创建数据库失败（可能已存在或权限不足）: {e}")

        # 连接到指定数据库
        self._conn = pymysql.connect(**self.config.to_dict(include_database=True))
        if verbose:
            print(f"[数据库] 已连接到 {self.config.user}@{self.config.host}:{self.config.port}/{self.config.database}")
        return self

    def init_tables(self):
        """初始化所有表，并幂等补齐旧库缺失的双层状态列。"""
        if not self._conn:
            raise RuntimeError("数据库未连接，请先调用 connect()")
        with self._conn.cursor() as cursor:
            for ddl in ALL_TABLES:
                cursor.execute(ddl)
            detail_columns = {
                "detail_status": "VARCHAR(32) NULL COMMENT '附件明细状态'",
                "detail_error_message": "TEXT COMMENT '附件明细解析或提取错误'",
            }
            for column, definition in detail_columns.items():
                cursor.execute(
                    "SHOW COLUMNS FROM announcements LIKE %s",
                    (column,),
                )
                if not cursor.fetchone():
                    cursor.execute(
                        f"ALTER TABLE announcements ADD COLUMN {column} {definition}"
                    )
            self._expand_detail_text_columns(cursor)
        self._conn.commit()
        print(f"[数据库] 已初始化 {len(ALL_TABLES)} 张表")

    @staticmethod
    def _expand_detail_text_columns(cursor):
        """只扩容旧附件明细列，不缩小字段，也不改基础层。

        名称扩容时使用前缀索引，避免 utf8mb4 全列索引超过 MySQL 限制。
        已更宽的 VARCHAR/TEXT 原样保留；不猜测未知表结构。
        """
        columns = (
            ("product_name", 1024, "产品服务名称"),
            ("spec_model", 8192, "规格型号"),
        )
        for name, capacity, comment in columns:
            cursor.execute("SHOW COLUMNS FROM extracted_entity_details LIKE %s", (name,))
            column = cursor.fetchone()
            kind = str(column.get("Type", "")).lower() if column else ""
            if kind in ("text", "mediumtext", "longtext"):
                continue
            width = re.fullmatch(r"varchar\((\d+)\)", kind)
            if not width:
                raise RuntimeError(f"附件明细字段 {name} 类型异常，拒绝自动改写")
            if int(width[1]) >= capacity:
                continue
            clauses = [f"MODIFY COLUMN {name} VARCHAR({capacity}) DEFAULT '' COMMENT '{comment}'"]
            if name == "product_name":
                cursor.execute("SHOW INDEX FROM extracted_entity_details WHERE Key_name = 'idx_detail_product'")
                index = cursor.fetchall()
                if index and (len(index) != 1 or index[0].get("Column_name") != name
                              or index[0].get("Non_unique") != 1 or index[0].get("Seq_in_index") != 1):
                    raise RuntimeError("附件明细名称索引结构异常，拒绝自动改写")
                prefix = index[0].get("Sub_part") if index else None
                if not index or prefix is None or prefix > 512:
                    if index:
                        clauses.append("DROP INDEX idx_detail_product")
                    clauses.append("ADD INDEX idx_detail_product (product_name(512))")
            cursor.execute("ALTER TABLE extracted_entity_details " + ", ".join(clauses))

    @contextmanager
    def transaction(self):
        """把多个 DAO 写入包装为同一事务，异常时完整回滚。"""
        if not self._conn:
            raise RuntimeError("数据库未连接，请先调用 connect()")
        self._conn.begin()
        try:
            yield self
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    @contextmanager
    def cursor(self):
        """获取游标（上下文管理器）"""
        if not self._conn:
            raise RuntimeError("数据库未连接，请先调用 connect()")
        cur = self._conn.cursor()
        try:
            yield cur
        finally:
            cur.close()

    def close(self, verbose: bool = True):
        """关闭连接"""
        if self._conn:
            self._conn.close()
            self._conn = None
            if verbose:
                print("[数据库] 连接已关闭")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
