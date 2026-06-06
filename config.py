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

# ============ 知识库配置 ============
# 文档加载
SUPPORTED_FORMATS = ['.md', '.txt', '.pdf', '.html']
KB_EMBEDDINGS_PROVIDER = "local"  # "local" 或 "deepseek_api" 或 "openai"

# 向量化模型（轻量）
EMBEDDINGS_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# 文本分割
CHUNK_SIZE = 500              # 一块的 token 数
CHUNK_OVERLAP = 50            # 重叠的 token 数
SEMANTIC_CHUNKING = True       # 语义感知分块（按标题/段落拆分，保留语境）

# Chroma 数据库
CHROMA_DB_PATH = os.path.join(DATA_DIR, "kb.db")
CHROMA_COLLECTION = "documents"

# 知识库元数据
KB_METADATA_FILE = os.path.join(CACHE_DIR, "kb_metadata.json")
EMBEDDINGS_CACHE_FILE = os.path.join(CACHE_DIR, "embeddings.pkl")

# 向量存储后端
VECTOR_STORE = os.getenv("VECTOR_STORE", "chroma")  # "chroma" 或 "faiss"
FAISS_INDEX_PATH = os.path.join(DATA_DIR, "faiss_index.bin")

# OpenAI / 兼容 Embeddings API
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "https://api.openai.com/v1")
OPENAI_EMBEDDINGS_MODEL = os.getenv("OPENAI_EMBEDDINGS_MODEL", "text-embedding-3-small")

# 混合检索（BM25 + 向量）
HYBRID_SEARCH_ENABLED = True   # 是否启用混合检索
BM25_WEIGHT = 0.3              # BM25 权重（0~1），剩余为向量权重

# ============ RAG（检索增强生成）配置 ============
RAG_ENABLED = True            # 是否默认启用 RAG 模式
RAG_TOP_K = 3                 # 每次检索返回的文档块数

# RAG 增强版 system prompt 模板
# {system_prompt} 会替换为原始 SYSTEM_PROMPT
# {context} 会替换为检索到的文档片段
RAG_SYSTEM_PROMPT_TEMPLATE = """{system_prompt}

## 参考知识库（从文档中检索到的相关内容）
请优先根据以下参考内容回答问题。如果参考内容不足以回答，请如实告知并基于你的知识补充。

{context}

---"""

# RAG 检索结果的格式化模板（每条）
RAG_CONTEXT_ITEM_TEMPLATE = """[来源: {source} (块{chunk}) 相似度: {score:.2f}]
{content}"""
