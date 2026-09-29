"""
任务三平台后端：FastAPI 服务入口

启动方式（在 task1_entity_extraction 目录下执行）：
  python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

接口文档：
  http://127.0.0.1:8000/docs
"""
import sys
from pathlib import Path

# 保证能找到任务一包内的模块（config / database / models 等）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import task1, task3

app = FastAPI(
    title="招采标讯智能分析引擎 API",
    description="任务一：实体识别与自动化提取；任务二：用户画像建模与关系分析",
    version="0.1.0",
)

# 前后端联调必备：允许前端开发服务器跨域访问
# Vue(Vite) 默认 5173 端口，另兼容 3000 端口
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(task1.router)
app.include_router(task3.router)


@app.get("/", include_in_schema=False)
def root():
    """根路径，指向接口文档"""
    return {"service": "招采标讯智能分析引擎 API", "docs": "/docs"}