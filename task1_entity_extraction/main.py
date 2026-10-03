"""
任务一：实体识别与自动化提取 - 命令行入口

使用方法：
  # 处理单个目录（包含多个公告）
  python main.py --input ./data/announcements

  # 处理单个 HTML 文件
  python main.py --input ./data/sample.html

  # 指定输出格式和文件名
  python main.py --input ./data --format excel --output my_results

  # 使用 DeepSeek 模型
  python main.py --input ./data --provider deepseek

  # 带评测（需要标准答案）
  python main.py --input ./data --ground_truth ./data/ground_truth.json
"""
import argparse
import sys
from pathlib import Path

# 确保能找到本包的模块
sys.path.insert(0, str(Path(__file__).resolve().parent))

from pipeline import EntityExtractionPipeline
import config


def main():
    parser = argparse.ArgumentParser(
        description="任务一：实体识别与自动化提取 - 政府采购公告信息提取"
    )
    parser.add_argument(
        "--input", "-i",
        required=True,
        help="输入路径：公告目录或单个 HTML 文件",
    )
    parser.add_argument(
        "--provider", "-p",
        choices=["qwen", "deepseek"],
        default=config.LLM_PROVIDER,
        help=f"LLM 提供商（默认: {config.LLM_PROVIDER}）",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="API Key（也可通过环境变量配置）",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="API 地址（覆盖默认）",
    )
    parser.add_argument(
        "--model", "-m",
        default=None,
        help="模型名称（如 qwen-plus / deepseek-chat）",
    )
    parser.add_argument(
        "--format", "-f",
        choices=["csv", "excel", "json"],
        default=config.OUTPUT_FORMAT,
        help=f"输出格式（默认: {config.OUTPUT_FORMAT}）",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="输出文件名（不含扩展名）",
    )
    parser.add_argument(
        "--ground_truth", "-g",
        default=None,
        help="标准答案 JSON 路径（用于评测）",
    )
    parser.add_argument(
        "--no-extra",
        action="store_true",
        help="不提取任务二需要的额外字段",
    )
    parser.add_argument(
        "--workers", "-w",
        type=int,
        default=config.PROCESS_MAX_WORKERS,
        help=f"并发线程数（默认: {config.PROCESS_MAX_WORKERS}）",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="断点续跑：跳过已成功处理的公告，仅重跑失败项并合并历史结果",
    )

    args = parser.parse_args()

    # 检查输入路径
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"[错误] 输入路径不存在: {args.input}")
        sys.exit(1)

    # 创建 pipeline
    try:
        pipeline = EntityExtractionPipeline(
            llm_provider=args.provider,
            api_key=args.api_key,
            base_url=args.base_url,
            model=args.model,
            extract_extra=not args.no_extra,
            max_workers=args.workers,
        )
    except Exception as e:
        print(f"[错误] 初始化失败: {e}")
        print("\n请检查 API Key 配置。可以通过以下方式配置：")
        print("  1. 创建 .env 文件，设置 QWEN_API_KEY=xxx 或 DEEPSEEK_API_KEY=xxx")
        print("  2. 设置环境变量")
        print("  3. 命令行传入 --api-key xxx")
        sys.exit(1)

    # 运行
    pipeline.run(
        input_path=str(input_path),
        output_filename=args.output,
        output_format=args.format,
        ground_truth_path=args.ground_truth,
        resume=args.resume,
    )


if __name__ == "__main__":
    main()
