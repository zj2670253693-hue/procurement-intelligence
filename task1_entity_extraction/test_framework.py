"""快速测试脚本：验证框架各模块是否正常工作"""
import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

def test_imports():
    print("=== 1. 测试模块导入 ===")
    from config import REQUIRED_FIELDS, FIELD_LABELS
    print(f"核心字段 ({len(REQUIRED_FIELDS)} 个):")
    for f in REQUIRED_FIELDS:
        print(f"  - {FIELD_LABELS[f]} ({f})")
    print("  ✅ 配置模块导入成功")

    from models.schemas import ExtractedEntity, ExtractionResult, AnnouncementContent
    print("  ✅ 数据模型模块导入成功")

    from data_loader.html_parser import HTMLAnnouncementParser
    from data_loader.attachment_parser import AttachmentParser
    print("  ✅ 数据加载模块导入成功")

    from extractor.prompts import build_extraction_prompt, build_extra_info_prompt
    print("  ✅ Prompt 模板模块导入成功")

    from output.writer import ResultWriter
    print("  ✅ 输出模块导入成功")

    from evaluation.metrics import Evaluator
    print("  ✅ 评测模块导入成功")

    from database.connection import DatabaseManager, DatabaseConfig
    from database.dao import DataRepository
    from database.schema import ALL_TABLES
    print(f"  ✅ 数据库模块导入成功（预定义 {len(ALL_TABLES)} 张表）")


def test_html_parser():
    print("\n=== 2. 测试 HTML 解析 ===")
    from data_loader.html_parser import HTMLAnnouncementParser
    parser = HTMLAnnouncementParser()
    content = parser.parse_file("sample_data/sample_announcement.html")
    print(f"  公告ID: {content.announcement_id}")
    print(f"  标题: {content.title[:40]}...")
    print(f"  文本长度: {len(content.html_text)} 字符")
    print(f"  表格数量: {len(content.html_tables)}")
    for i, table in enumerate(content.html_tables):
        print(f"  表格{i+1}: {len(table)} 行")
        if table:
            print(f"    表头: {table[0][:3]}...")
    print("  ✅ HTML 解析正常")


def test_evaluation():
    print("\n=== 3. 测试评测模块 ===")
    from evaluation.metrics import Evaluator
    from models.schemas import ExtractedEntity, ExtractionResult

    evaluator = Evaluator()

    # 模拟预测结果（部分正确）
    predictions = [
        ExtractionResult(
            announcement_id="sample_announcement",
            entities=[
                ExtractedEntity(
                    product_name="应用服务器", category="A02010103 服务器",
                    brand="华为", spec_model="2288H V5",
                    unit_price="35,000.00", quantity="10台", total_price="350,000.00"
                ),
                ExtractedEntity(
                    product_name="数据库服务器", category="A02010103 服务器",
                    brand="华为", spec_model="2288H V5",
                    unit_price="58,000.00", quantity="5台", total_price="290,000.00"
                ),
            ]
        )
    ]

    with open("sample_data/ground_truth_sample.json", "r", encoding="utf-8") as f:
        ground_truths = json.load(f)

    metrics = evaluator.evaluate(predictions, ground_truths)
    evaluator.print_report(metrics)
    print("  ✅ 评测模块正常")


def test_database_schema():
    print("\n=== 5. 测试数据库表结构 ===")
    from database.schema import (
        CREATE_TABLE_ANNOUNCEMENTS, CREATE_TABLE_ENTITIES,
        CREATE_TABLE_PROJECTS, CREATE_TABLE_BIDDERS, ALL_TABLES
    )
    print(f"  公告表 announcements: {'CREATE TABLE' in CREATE_TABLE_ANNOUNCEMENTS}")
    print(f"  标的物表 extracted_entities: {'CREATE TABLE' in CREATE_TABLE_ENTITIES}")
    print(f"  项目关系表 project_relations: {'CREATE TABLE' in CREATE_TABLE_PROJECTS}")
    print(f"  投标人表 bidders: {'CREATE TABLE' in CREATE_TABLE_BIDDERS}")
    print(f"  共 {len(ALL_TABLES)} 张表的 DDL 已定义")
    print("  ✅ 数据库表结构定义正常")


def test_prompts():
    print("\n=== 4. 测试 Prompt 模板 ===")
    from extractor.prompts import build_extraction_prompt, build_extra_info_prompt
    sys_prompt, user_prompt = build_extraction_prompt("测试文本", has_tables=False)
    print(f"  System Prompt 长度: {len(sys_prompt)} 字符")
    print(f"  User Prompt 包含测试文本: {'测试文本' in user_prompt}")
    print("  ✅ Prompt 模板正常")


if __name__ == "__main__":
    try:
        test_imports()
        test_html_parser()
        test_evaluation()
        test_prompts()
        test_database_schema()
        print("\n" + "=" * 50)
        print("🎉 所有测试通过！框架基础功能正常。")
        print("=" * 50)
        print("\n下一步：")
        print("  1. 配置 .env 中的 API Key 和数据库信息")
        print("  2. 运行: python main.py --input sample_data/sample_announcement.html")
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
