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

# 上下文检索增强 — 嵌入前为分块添加文档/章节前缀
CONTEXT_ENRICHMENT_ENABLED = True  # 是否启用上下文前缀富化
CONTEXT_ENRICHMENT_TEMPLATE = "[文档: {title} | 章节: {section_path}] {chunk_text}"

# 元数据过滤 — 可过滤字段及类型
# key: 用户侧过滤字段名, value: (存储 metadata key, 类型 "exact"/"range")
METADATA_FILTER_FIELDS = {
    "doc_type":    ("doc_type", "exact"),
    "source":      ("source", "exact"),
    "mtime_after": ("mtime", "gte"),
    "mtime_before":("mtime", "lte"),
}

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
RAG_CONTEXT_ITEM_TEMPLATE = """[来源: {source} | 类型: {doc_type} | 标题: {title} | 块{chunk} | 相似度: {score:.2f}]
{content}"""

# ============ Agent Loop（自主循环检索）配置 ============
AGENT_MODE_ENABLED = True       # 默认是否启用 Agent 模式（LLM 自主调用检索工具）
AGENT_MAX_LLM_ROUNDS = 5        # L1: 最大 LLM 对话轮次
AGENT_MAX_TOTAL_TOOL_CALLS = 10 # L2: 累计工具调用上限
AGENT_DUPLICATE_THRESHOLD = 0.85# L3: 重复查询 Jaccard 相似度阈值
AGENT_LOW_SCORE_THRESHOLD = 0.3 # L5: 低分熔断阈值（连续 3 次低于此分则熔断）
AGENT_CONTEXT_RATIO = 0.8       # L6: Token 预算告警比例（占模型上下文的 80%）
AGENT_MODEL_MAX_CONTEXT = 16000 # DeepSeek-chat 上下文窗口（保守估计）

# Agent 专用 system prompt
AGENT_SYSTEM_PROMPT = """你是一个知识库问答助手，拥有自主检索能力。你可以调用 search_knowledge_base 工具来查找知识库中的信息。

## 核心行为准则

1. **先检索再回答**：对于需要从文档中查找信息的问题，先调用 search_knowledge_base 工具检索。
2. **评估检索质量**：仔细阅读每条结果的[检索评估]提示和分数：
   - ★★★ 高相关（≥0.6）：结果可信，直接基于结果回答
   - ★★☆ 中等相关（0.3~0.6）：结果部分相关，可补充一次检索或基于现有信息回答
   - ★☆☆ 低相关（<0.3）：结果可信度低，应尝试不同策略重新检索
3. **最多检索 2-3 次**：如果第一次检索不理想，可以：
   - 换不同的关键词（同义词、更宽泛或更具体的表述）
   - 调整 top_k 参数获取更多结果
   - 使用 filters 缩小范围（如限定 doc_type）
4. **知道何时停止**：
   - 检索结果充分 → 直接回答，不要无谓重复检索
   - 多次检索仍不理想 → 如实告知用户"知识库中未找到相关信息"，可基于你的常识补充
   - **禁止**用完全相同或高度相似的关键词重复搜索

## 不需要检索的情况
- 简单寒暄（"你好"、"谢谢"）
- 纯常识性问题（"地球绕太阳转吗"）
- 纯代码编写请求
- 对你自己能力的询问

## 回答要求
- 回答时注明信息来源（如"根据《Python教程》文档..."，如果你回答的依据并非来自某来源，请不要标注，并说明这一点）
- 检索分数高的结果也未必完全相关，如果你给出的回答并非基于检索结果，请明确说明
- 如果信息来自你的常识而非知识库，请明确说明
- 保持回答准确、简洁、友好"""
