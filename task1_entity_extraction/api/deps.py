"""
FastAPI 依赖项：请求级数据库连接

pymysql 的连接不是线程安全的，而 FastAPI 会把同步接口放到线程池执行，
因此这里为每个请求单独建立一条连接，用完即关，避免多线程共享连接。
"""
from typing import Iterator

import config
from database.connection import DatabaseManager, DatabaseConfig
from database.dao import DataRepository


def get_repository() -> Iterator[DataRepository]:
    """请求级数据仓库依赖，请求结束后自动关闭连接"""
    db = DatabaseManager(DatabaseConfig(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        database=config.DB_NAME,
    )).connect(create_db=False, verbose=False)

    try:
        yield DataRepository(db)
    finally:
        db.close(verbose=False)