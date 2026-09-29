"""
MySQL 数据库连接管理
"""
import os
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
        """初始化所有表"""
        if not self._conn:
            raise RuntimeError("数据库未连接，请先调用 connect()")
        with self._conn.cursor() as cursor:
            for ddl in ALL_TABLES:
                cursor.execute(ddl)
        print(f"[数据库] 已初始化 {len(ALL_TABLES)} 张表")

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
