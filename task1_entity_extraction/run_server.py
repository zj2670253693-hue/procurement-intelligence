"""
后端服务启动入口

相比直接调用 uvicorn，本脚本会把项目目录加入模块搜索路径，
因此可以在任意目录下执行，不会出现 "No module named 'api'"。

用法：
  python run_server.py                 # 默认 0.0.0.0:8000
  python run_server.py --port 9000
  python run_server.py --reload        # 开发模式，改代码自动重启

接口文档：http://127.0.0.1:8000/docs
"""
import argparse
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_DIR))

import uvicorn


def main():
    parser = argparse.ArgumentParser(description="启动招采标讯智能分析引擎后端服务")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址（默认 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8000, help="监听端口（默认 8000）")
    parser.add_argument("--reload", action="store_true", help="开发模式：代码变更自动重启")
    args = parser.parse_args()

    print(f"[服务] 启动中... {args.host}:{args.port}")
    print(f"[服务] 接口文档: http://127.0.0.1:{args.port}/docs")

    uvicorn.run(
        "api.main:app",
        host=args.host,
        port=args.port,
        app_dir=str(BASE_DIR),  # 关键：让 uvicorn 在项目目录下解析 api 包
        reload=args.reload,
    )


if __name__ == "__main__":
    main()