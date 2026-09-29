"""
将已提取结果导入 MySQL（不重新调用大模型）

用途：任务一结果已由 pipeline 输出到 output/raw_results.json，
本脚本负责把它写入 MySQL，供 FastAPI 查询接口使用。

用法：
  python import_to_db.py                         # 只导入 JSON 中的结构化结果
  python import_to_db.py ../benchmark_data/combined   # 同时回填 HTML 原文
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config
from database.connection import DatabaseManager, DatabaseConfig
from database.dao import DataRepository
from data_loader.html_parser import HTMLAnnouncementParser
from models.schemas import ExtractedEntity, ExtractionResult


def main():
    raw_path = config.OUTPUT_DIR / "raw_results.json"
    if not raw_path.exists():
        print(f"[错误] 未找到 {raw_path}，请先运行 main.py 完成提取")
        sys.exit(1)

    with open(raw_path, "r", encoding="utf-8") as f:
        raw_results = json.load(f)

    # 可选的 HTML 目录（用于回填原文）
    html_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    html_parser = HTMLAnnouncementParser() if html_dir else None
    if html_dir and not html_dir.exists():
        print(f"[错误] HTML 目录不存在: {html_dir}")
        sys.exit(1)

    # 连接数据库并建表
    db = DatabaseManager(DatabaseConfig(
        host=config.DB_HOST,
        port=config.DB_PORT,
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        database=config.DB_NAME,
    )).connect(create_db=True)
    db.init_tables()
    repository = DataRepository(db)

    total = len(raw_results)
    print(f"\n[导入] 共 {total} 篇公告待入库...")

    for i, item in enumerate(raw_results, 1):
        aid = item["announcement_id"]
        entities = [ExtractedEntity(**e) for e in item.get("entities", [])]
        result = ExtractionResult(
            announcement_id=aid,
            entities=entities,
            extra_info=item.get("extra_info") or {},
            success=item.get("success", True),
            error_message=item.get("error") or "",
        )

        raw_text, file_path = "", ""
        if html_dir:
            html_file = html_dir / f"{aid}.html"
            if html_file.exists():
                file_path = str(html_file)
                try:
                    raw_text = html_parser.parse_file(str(html_file)).html_text
                except Exception:
                    pass

        repository.save_extraction_result(result, raw_text=raw_text, file_path=file_path)

        if i % 200 == 0 or i == total:
            print(f"  进度 {i}/{total}")

    # 统计
    entity_count = repository.entities.count_all()
    announcement_count = len(repository.announcements.list_all())
    print(f"\n[完成] 公告 {announcement_count} 篇，标的物 {entity_count} 条已入库")
    db.close()


if __name__ == "__main__":
    main()