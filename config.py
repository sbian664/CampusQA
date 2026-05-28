"""
项目配置文件
"""
import os
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

# ============ LLM 配置 ============
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "deepseek")  # deepseek 或 local

# DeepSeek API 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_API_BASE = os.getenv("DEEPSEEK_API_BASE", "https://api.deepseek.com/v1")
DEEPSEEK_MODEL = os.getenv("MODEL_NAME", "deepseek-chat")

# 本地模型配置（预留）
LOCAL_MODEL_BASE = os.getenv("LOCAL_MODEL_BASE", "http://localhost:8000/v1")
LOCAL_MODEL_NAME = os.getenv("LOCAL_MODEL_NAME", "local-model")

# ============ 对话配置 ============
MAX_TOKENS = int(os.getenv("MAX_TOKENS", "2000"))
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.7"))

# ============ 路径配置 ============
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DOCUMENTS_DIR = os.path.join(DATA_DIR, "documents")
CACHE_DIR = os.path.join(DATA_DIR, "cache")

# ============ 系统提示词 ============
SYSTEM_PROMPT = """你是一个有用的AI助手。
- 回答准确、简洁
- 如果不确定，请说明
- 保持友好和专业的语气"""
