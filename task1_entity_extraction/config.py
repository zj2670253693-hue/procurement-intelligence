"""
任务一：实体识别与自动化提取 - 全局配置
"""
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ============================================================
# 项目路径
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
SAMPLE_DATA_DIR = BASE_DIR / "sample_data"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

# ============================================================
# LLM 模型配置（Qwen / DeepSeek API）
# ============================================================
# 支持 Qwen 和 DeepSeek 两种 API，按赛事要求优先使用国产大模型
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "qwen")  # "qwen" or "deepseek"

# Qwen 配置（通义千问）
QWEN_API_KEY = os.getenv("QWEN_API_KEY", "")
QWEN_BASE_URL = os.getenv("QWEN_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen-plus")  # qwen-turbo / qwen-plus / qwen-max

# DeepSeek 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")  # deepseek-chat / deepseek-coder

# 生成参数
LLM_TEMPERATURE = float(os.getenv("LLM_TEMPERATURE", "0.1"))
LLM_MAX_TOKENS = int(os.getenv("LLM_MAX_TOKENS", "8192"))
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "120"))

# 重试配置
LLM_MAX_RETRIES = int(os.getenv("LLM_MAX_RETRIES", "3"))
LLM_RETRY_DELAY = float(os.getenv("LLM_RETRY_DELAY", "2.0"))

# ============================================================
# 提取字段定义（任务一要求的 7 个核心字段）
# ============================================================
REQUIRED_FIELDS = [
    "product_name",      # 产品服务名称
    "category",          # 品目
    "brand",             # 品牌（产品供应商）
    "spec_model",        # 规格型号
    "unit_price",        # 单价
    "quantity",          # 数量
    "total_price",       # 总价
]

# 字段中文映射（用于输出和展示）
FIELD_LABELS = {
    "product_name": "产品服务名称",
    "category": "品目",
    "brand": "品牌（产品供应商）",
    "spec_model": "规格型号",
    "unit_price": "单价",
    "quantity": "数量",
    "total_price": "总价",
}

# 任务二需要的额外字段（赛题注：可能需要额外提取以支撑关系建模）
EXTRA_FIELDS = [
    "project_name",       # 项目名称
    "procurement_unit",   # 采购单位
    "winning_supplier",   # 中标供应商
    "bidders",            # 参与投标人
    "project_amount",     # 项目金额
]

# ============================================================
# 数据处理配置
# ============================================================
# 2核4GB 比赛环境默认仅保留 2 个并发任务。所有批处理入口共享该值，
# 避免 API 上传、命令行提取和附件分析各自使用过高并发。
PROCESS_MAX_WORKERS = max(1, int(os.getenv("PROCESS_MAX_WORKERS", "2")))

# 支持的附件格式
SUPPORTED_ATTACHMENT_FORMATS = [".doc", ".docx", ".xlsx", ".xls", ".pdf", ".pptx", ".txt"]

# 文本分块配置（当文本过长时分块处理）
MAX_TEXT_LENGTH_PER_REQUEST = 8000  # 单次请求最大文本长度
TEXT_CHUNK_OVERLAP = 500             # 分块重叠字符数

# PDF 解析与 OCR。普通长 PDF 抽取“前部 + 均匀中部 + 尾部”，
# 报价/明细类高价值附件提高页数上限，避免关键表格恰好在后半部。
PDF_MAX_TEXT_PAGES = int(os.getenv("PDF_MAX_TEXT_PAGES", "20"))
PDF_RELEVANT_MAX_TEXT_PAGES = int(os.getenv("PDF_RELEVANT_MAX_TEXT_PAGES", "60"))
PDF_RELEVANT_NAME_PATTERN = os.getenv(
    "PDF_RELEVANT_NAME_PATTERN",
    r"报价|明细|中标|成交|分项|清单|一览表|最终承诺",
)

# 仅对无文字层（或文字极少）的 PDF 页执行 OCR，控制速度和内存。
OCR_ENABLED = os.getenv("OCR_ENABLED", "true").lower() == "true"
OCR_MAX_PAGES_PER_PDF = int(os.getenv("OCR_MAX_PAGES_PER_PDF", "5"))
OCR_MIN_TEXT_CHARS = int(os.getenv("OCR_MIN_TEXT_CHARS", "30"))
OCR_RENDER_SCALE = float(os.getenv("OCR_RENDER_SCALE", "2.0"))

# ============================================================
# 数据库配置（MySQL）
# ============================================================
DB_ENABLED = os.getenv("DB_ENABLED", "true").lower() == "true"
DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASSWORD = os.getenv("DB_PASSWORD", "")
DB_NAME = os.getenv("DB_NAME", "procurement_kg")

# ============================================================
# 输出配置
# ============================================================
OUTPUT_FORMAT = os.getenv("OUTPUT_FORMAT", "csv")  # csv / excel / json
OUTPUT_FILENAME = os.getenv("OUTPUT_FILENAME", "extraction_results")
