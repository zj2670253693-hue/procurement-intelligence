"""测试 DeepSeek API 连接"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))

from dotenv import load_dotenv
load_dotenv()

import os
from openai import OpenAI

client = OpenAI(
    api_key=os.getenv("DEEPSEEK_API_KEY"),
    base_url=os.getenv("DEEPSEEK_BASE_URL"),
    timeout=30,
)

print("正在测试 DeepSeek API 连接...")
try:
    resp = client.chat.completions.create(
        model="deepseek-chat",
        messages=[{"role": "user", "content": "你好，请回复 API连接成功 这五个字"}],
        max_tokens=50,
        temperature=0.1,
    )
    print(f"API 响应: {resp.choices[0].message.content}")
    print("OK: DeepSeek API 连接成功！")
except Exception as e:
    print(f"FAIL: API 连接失败: {e}")
