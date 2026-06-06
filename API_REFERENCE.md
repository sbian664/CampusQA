# API 参考文档

## 📚 总览

本文档详细说明知识库 Agent 的核心API接口、使用方法和示例。

---

## 🔧 核心模块

### 1. session.py - 会话管理（第二阶段新增）

#### 概述
提供对话历史管理、会话持久化等功能。支持多轮对话的完整记忆系统。

#### 类定义

##### Session

```python
class Session:
    """对话会话类 - 管理对话历史和上下文"""
    
    def __init__(self, session_id: str = None, max_history: int = 20):
        """
        初始化会话
        
        Args:
            session_id (str): 会话ID，如果为None则使用时间戳生成
            max_history (int): 保留的最大历史消息数（防止token溢出，默认20）
        
        Examples:
            >>> session = Session()
            >>> session = Session(session_id="custom_id", max_history=50)
        """
    
    def add_message(self, role: str, content: str) -> None:
        """
        添加消息到历史
        
        Args:
            role (str): 消息角色 ("user" 或 "assistant")
            content (str): 消息内容
        
        Raises:
            ValueError: 如果 role 不合法
        
        Examples:
            >>> session = Session()
            >>> session.add_message("user", "你好")
            >>> session.add_message("assistant", "你好！很高兴认识你")
        """
    
    def get_history(self, include_timestamp: bool = False) -> List[Dict]:
        """
        获取对话历史（OpenAI API 兼容格式）
        
        Args:
            include_timestamp (bool): 是否包含时间戳
        
        Returns:
            List[Dict]: 消息列表
                      格式：[{"role": "user", "content": "..."}, ...]
        
        Examples:
            >>> session = Session()
            >>> session.add_message("user", "你好")
            >>> history = session.get_history()
            >>> print(history)
            [{'role': 'user', 'content': '你好'}]
        """
    
    def get_last_exchange(self) -> Optional[Dict]:
        """
        获取最后一次对话交换（用户消息 + AI回复）
        
        Returns:
            Dict: 包含 user 和 assistant 的最后一次交换
                 {"user": "用户问题", "assistant": "AI回复"}
                 如果无交换则返回None
        
        Examples:
            >>> session = Session()
            >>> session.add_message("user", "什么是AI?")
            >>> session.add_message("assistant", "AI是人工智能...")
            >>> exchange = session.get_last_exchange()
            >>> print(exchange["user"])
            '什么是AI?'
        """
    
    def clear() -> None:
        """
        清空对话历史
        
        Examples:
            >>> session = Session()
            >>> session.add_message("user", "你好")
            >>> session.clear()
            >>> len(session.get_history())
            0
        """
    
    def get_context_summary(self, max_chars: int = 500) -> str:
        """
        获取对话上下文摘要（用于调试或显示）
        
        Args:
            max_chars (int): 摘要最大字符数（默认500）
        
        Returns:
            str: 格式化的上下文摘要，包含：
                - 会话ID
                - 消息数
                - 创建/更新时间
                - 对话内容预览
        
        Examples:
            >>> session = Session()
            >>> session.add_message("user", "你好")
            >>> print(session.get_context_summary())
        """
    
    def save(self, filename: str = None) -> str:
        """
        保存会话到文件（JSON格式）
        
        Args:
            filename (str): 文件名，如果为None则使用会话ID + .json
        
        Returns:
            str: 保存的文件路径
        
        Raises:
            Exception: 保存失败时抛出异常
        
        Examples:
            >>> session = Session()
            >>> session.add_message("user", "你好")
            >>> path = session.save()
            >>> print(path)
            D:\Projects\Agent\data\cache\20260529_121318.json
        """
    
    def load(self, filename: str = None) -> bool:
        """
        从文件加载会话
        
        Args:
            filename (str): 文件名，如果为None则使用会话ID + .json
        
        Returns:
            bool: 是否加载成功
        
        Examples:
            >>> session = Session("old_session_id")
            >>> if session.load():
            ...     print("加载成功")
        """
    
    @staticmethod
    def list_saved_sessions() -> List[str]:
        """
        列出所有保存的会话文件
        
        Returns:
            List[str]: 保存的会话JSON文件列表
        
        Examples:
            >>> sessions = Session.list_saved_sessions()
            >>> print(sessions)
            ['20260529_121318.json', '20260529_120000.json']
        """
```

#### 会话数据格式

```json
{
  "session_id": "20260529_121318",
  "messages": [
    {
      "role": "user",
      "content": "你好",
      "timestamp": "2026-05-29T12:13:27.245451"
    },
    {
      "role": "assistant",
      "content": "你好，很高兴认识你",
      "timestamp": "2026-05-29T12:13:27.300000"
    }
  ],
  "metadata": {
    "created_at": "2026-05-29T12:13:18.561604",
    "updated_at": "2026-05-29T12:13:34.400713",
    "message_count": 2
  }
}
```

#### 使用示例

```python
from src.session import Session
from src.chatbot import Chatbot

# 创建新会话
session = Session()

# 创建机器人
chatbot = Chatbot()

# 多轮对话
user_input = "你好，我叫张三"
response = chatbot.chat_with_history(user_input, session.get_history())
session.add_message("user", user_input)
session.add_message("assistant", response)

# 查看历史
print(session.get_history())

# 保存会话
path = session.save()
print(f"会话已保存: {path}")

# 加载会话
new_session = Session("old_session_id")
if new_session.load():
    print("会话加载成功")
    response = chatbot.chat_with_history("你还记得我叫什么吗?", new_session.get_history())
```

---

### 2. llm_client.py - LLM 客户端

#### 概述
提供统一的 LLM 客户端接口，支持多个提供商（DeepSeek、本地模型等）。

#### 类定义

##### LLMClient（基类）
```python
class LLMClient:
    """LLM 客户端基类"""
    
    def send_message(self, messages: List[Dict], **kwargs) -> str:
        """
        发送消息到 LLM
        
        Args:
            messages (List[Dict]): 消息列表，格式为 [{"role": "...", "content": "..."}, ...]
            **kwargs: 额外参数（max_tokens, temperature 等）
        
        Returns:
            str: LLM 响应文本
        
        Raises:
            Exception: API 调用失败时抛出异常
        """
```

##### DeepSeekClient
```python
class DeepSeekClient(LLMClient):
    """DeepSeek API 客户端"""
    
    def __init__(self):
        """初始化 DeepSeek 客户端
        
        Raises:
            ValueError: DEEPSEEK_API_KEY 未设置
        """
        # 需要配置环境变量：DEEPSEEK_API_KEY
```

##### LocalModelClient
```python
class LocalModelClient(LLMClient):
    """本地模型客户端（预留接口）"""
    
    def __init__(self):
        """初始化本地模型客户端
        
        需要配置环境变量：
        - LOCAL_MODEL_BASE
        - LOCAL_MODEL_NAME
        """
```

#### 工厂函数

```python
def create_llm_client(provider: str = None) -> LLMClient:
    """
    工厂函数：根据提供商创建 LLM 客户端
    
    Args:
        provider (str): LLM 提供商
                       - "deepseek": DeepSeek API
                       - "local": 本地模型
                       - None: 使用 config.py 中的 LLM_PROVIDER
    
    Returns:
        LLMClient: 对应的 LLM 客户端实例
    
    Raises:
        ValueError: 不支持的提供商
    
    Examples:
        >>> from src.llm_client import create_llm_client
        >>> client = create_llm_client("deepseek")
        >>> response = client.send_message([{"role": "user", "content": "hello"}])
    """
```

#### 使用示例

```python
from src.llm_client import create_llm_client

# 创建 DeepSeek 客户端
client = create_llm_client("deepseek")

# 构建消息
messages = [
    {"role": "system", "content": "你是一个有用的助手"},
    {"role": "user", "content": "什么是Python?"}
]

# 发送消息
response = client.send_message(messages, max_tokens=500, temperature=0.7)
print(response)
```

---

### 2. chatbot.py - 对话机器人

#### 概述
提供对话机器人的核心功能，支持单轮和多轮对话。

#### 类定义

##### Chatbot

```python
class Chatbot:
    """基础对话机器人"""
    
    def __init__(self, llm_provider: str = None):
        """
        初始化对话机器人
        
        Args:
            llm_provider (str): LLM 提供商
                               - "deepseek": DeepSeek API
                               - "local": 本地模型
                               - None: 使用 config.py 中的 LLM_PROVIDER
        
        Examples:
            >>> chatbot = Chatbot("deepseek")
            >>> chatbot = Chatbot()  # 使用默认提供商
        """
    
    def chat(self, user_message: str) -> str:
        """
        单轮对话
        
        Args:
            user_message (str): 用户输入
        
        Returns:
            str: 机器人回复
        
        Examples:
            >>> chatbot = Chatbot()
            >>> response = chatbot.chat("你好")
            >>> print(response)
        """
    
    def chat_with_history(self, user_message: str, history: List[Dict]) -> str:
        """
        带对话历史的多轮对话（第二阶段功能）
        
        Args:
            user_message (str): 当前用户输入
            history (List[Dict]): 对话历史
                                 格式: [
                                     {"role": "user", "content": "..."},
                                     {"role": "assistant", "content": "..."}
                                 ]
        
        Returns:
            str: 机器人回复
        
        Examples:
            >>> chatbot = Chatbot()
            >>> history = [
            ...     {"role": "user", "content": "你叫什么?"},
            ...     {"role": "assistant", "content": "我是AI助手"}
            ... ]
            >>> response = chatbot.chat_with_history("你能做什么?", history)
        """
```

#### 消息格式

```python
# 标准消息格式（OpenAI 兼容）
message = {
    "role": "system" | "user" | "assistant",
    "content": "消息文本"
}

# 对话历史示例
history = [
    {"role": "user", "content": "第一个问题"},
    {"role": "assistant", "content": "第一个回答"},
    {"role": "user", "content": "第二个问题"},
    {"role": "assistant", "content": "第二个回答"}
]
```

#### 使用示例

```python
from src.chatbot import Chatbot

# 创建对话机器人
chatbot = Chatbot()

# 单轮对话
response = chatbot.chat("你好，请介绍一下自己")
print(response)

# 多轮对话（第二阶段功能）
history = [
    {"role": "user", "content": "什么是机器学习?"},
    {"role": "assistant", "content": "机器学习是..."}
]
response = chatbot.chat_with_history("它有什么应用?", history)
print(response)
```
    
    def chat_with_rag(self, user_message: str, history: List[Dict]) -> str:
        """
        带 RAG 检索增强的多轮对话（第四阶段新增）
        
        流程：
        1. 在知识库中检索与问题相关的文档块
        2. 将检索结果格式化为上下文文本
        3. 构建增强版 system prompt 注入检索上下文
        4. 调用 LLM 生成基于知识库的回复
        
        Args:
            user_message (str): 当前用户输入
            history (List[Dict]): 对话历史
        
        Returns:
            str: 基于知识库增强的回复
            
        Note:
            如果 Chatbot 初始化时未传入 knowledge_base，
            自动降级为 chat_with_history() 普通对话。
        
        Examples:
            >>> from src.knowledge_base import KnowledgeBase
            >>> kb = KnowledgeBase()
            >>> kb.load_documents_from_dir()
            >>> chatbot = Chatbot(knowledge_base=kb)
            >>> response = chatbot.chat_with_rag("什么是监督学习?", [])
            >>> print(response)
            '根据文档 ml_basics.txt，监督学习是...'
        """

---

### 3. config.py - 配置管理

#### 概述
集中式配置管理，读取环境变量并提供全局配置。

#### 配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `LLM_PROVIDER` | str | deepseek | LLM提供商（deepseek/local） |
| `DEEPSEEK_API_KEY` | str | - | DeepSeek API 密钥（必需） |
| `DEEPSEEK_API_BASE` | str | https://api.deepseek.com/v1 | DeepSeek API 地址 |
| `DEEPSEEK_MODEL` | str | deepseek-chat | DeepSeek 模型名称 |
| `LOCAL_MODEL_BASE` | str | http://localhost:8000/v1 | 本地模型 API 地址 |
| `LOCAL_MODEL_NAME` | str | local-model | 本地模型名称 |
| `MAX_TOKENS` | int | 2000 | 最大生成 token 数 |
| `TEMPERATURE` | float | 0.7 | 回复温度（0-1，越高越随机） |
| `SYSTEM_PROMPT` | str | - | 系统提示词 |
| `BASE_DIR` | str | - | 项目根目录 |
| `DOCUMENTS_DIR` | str | - | 文档存放目录 |
| `CACHE_DIR` | str | - | 缓存目录 |

#### 使用示例

```python
from config import (
    LLM_PROVIDER, 
    DEEPSEEK_API_KEY,
    MAX_TOKENS,
    TEMPERATURE,
    SYSTEM_PROMPT,
    DOCUMENTS_DIR
)

# 读取配置
print(f"当前LLM提供商: {LLM_PROVIDER}")
print(f"最大Token数: {MAX_TOKENS}")
print(f"文档目录: {DOCUMENTS_DIR}")
```

#### 环境变量配置

在 `.env` 文件中配置：

```ini
# DeepSeek API 配置
DEEPSEEK_API_KEY=sk-xxx
DEEPSEEK_API_BASE=https://api.deepseek.com/v1

# 本地模型接口配置（预留）
LOCAL_MODEL_BASE=http://localhost:8000/v1
LOCAL_MODEL_NAME=local-model

# 对话配置
MODEL_NAME=deepseek-chat
MAX_TOKENS=2000
TEMPERATURE=0.7
```

---

## 🚀 快速开始

### 基本对话（单轮）

```python
from src.chatbot import Chatbot

# 初始化
chatbot = Chatbot()

# 对话
response = chatbot.chat("你好")
print(response)
```

### 多轮对话（带记忆）

```python
from src.chatbot import Chatbot
from src.session import Session

# 初始化
chatbot = Chatbot()
session = Session()

# 第一轮
user_msg1 = "我叫张三"
response1 = chatbot.chat_with_history(user_msg1, session.get_history())
session.add_message("user", user_msg1)
session.add_message("assistant", response1)

# 第二轮（AI 记得用户名）
user_msg2 = "我叫什么名字?"
response2 = chatbot.chat_with_history(user_msg2, session.get_history())
session.add_message("user", user_msg2)
session.add_message("assistant", response2)

# 保存会话
session.save()
```

### 命令行交互

```bash
# 运行
python main.py

# 支持的命令（以 / 开头）
你: /help              # 显示帮助信息
你: /history           # 查看对话历史
你: /summary           # 显示会话摘要
你: /save              # 保存会话
你: /load              # 加载会话
你: /clear             # 清空历史
你: /quit              # 退出程序

# 普通对话（直接输入消息）
你: hello              # 普通消息直接对话
你: 你好               # 支持中文
```

### 切换 LLM 提供商

```python
# 使用 DeepSeek
chatbot = Chatbot("deepseek")

# 使用本地模型（需要配置和启动本地服务）
chatbot = Chatbot("local")
```

---

## 📝 常见问题

### Q: 对话记忆有时间限制吗？

A: 没有。会话保存到文件后，可以随时加载恢复。只有当前会话的历史受 `max_history` 限制（默认20条消息）。

### Q: 如何加载之前保存的会话？

A: ```python
session = Session("20260529_121318")  # 使用会话ID
if session.load():
    print("加载成功")
    # 继续对话
```

### Q: 如何修改系统提示词？

A: 编辑 `config.py` 中的 `SYSTEM_PROMPT` 变量：

```python
SYSTEM_PROMPT = """你是一个专业的技术顾问。
- 回答准确专业
- 提供代码示例
- 保持友好态度"""
```

### Q: 如何增加模型的多样性？

A: 调整 `TEMPERATURE` 参数：
- 0.0：确定性回复，重复率高
- 0.7：平衡性回复（默认）
- 1.0+：高度随机性回复

### Q: 如何处理长对话的 token 溢出？

A: 调整 `MAX_TOKENS` 参数或使用 Session 的 `max_history` 功能自动截断历史：

```python
session = Session(max_history=10)  # 只保留最后10条消息
```

---

## 🔄 API 调用流程

```
user_input
    ↓
main.py (命令行交互)
    ↓
chatbot.chat(user_input)
    ↓
构建 messages 列表
    ↓
llm_client.send_message(messages)
    ↓
create_llm_client() 创建客户端
    ↓
DeepSeekClient.send_message()
    ↓
HTTP POST 请求到 DeepSeek API
    ↓
解析响应并返回
    ↓
显示结果给用户
```

---

## 📦 依赖关系

```
requirements.txt
├── python-dotenv>=1.0.0  (环境变量管理)
└── requests>=2.31.0      (HTTP 请求)

src/
├── llm_client.py (LLM 客户端)
│   └── 依赖: config.py, requests
├── chatbot.py (对话机器人)
│   └── 依赖: llm_client.py, config.py
└── __init__.py

main.py (入口)
└── 依赖: src/chatbot.py, config.py

config.py (配置)
└── 依赖: python-dotenv
```

---

## 📖 后续阶段的 API 预告

### 第三阶段 - DocumentLoader（文档加载）

```python
from src.document_loader import DocumentLoader
from src.knowledge_base import KnowledgeBase

# 加载文档
loader = DocumentLoader("data/documents")
docs = loader.load_all()  # 加载所有 .md 和 .txt 文件

# 管理知识库
kb = KnowledgeBase()
kb.add_documents(docs)
kb.build_index()

# 获取文档
doc = kb.get_document("filename.md")
```

### 第四阶段 - Retriever（文本检索）

```python
from src.text_processor import TextProcessor
from src.retriever import Retriever

# 文本分块
processor = TextProcessor()
chunks = processor.chunk_documents(docs, chunk_size=500)

# 检索相关内容
retriever = Retriever(kb)
relevant_chunks = retriever.retrieve("查询关键词", top_k=3)

# 在对话中使用
context = retriever.build_context(relevant_chunks)
response = chatbot.chat_with_context(user_query, context, session.get_history())
```

### 第五阶段 - VectorStore（向量检索）

```python
from src.embedder import Embedder
from src.vector_store import VectorStore

# 向量化
embedder = Embedder()
vectors = embedder.embed_chunks(chunks)

# 向量存储
vs = VectorStore("faiss")  # 或 "milvus"
vs.add_vectors(vectors, metadata=chunks)

# 向量检索
top_results = vs.search(user_query, top_k=5)
```

---

### 5. knowledge_base.py - 知识库管理（第三阶段新增）

#### 概述
文档向量化存储与检索系统。基于 Chroma 向量数据库 + sentence-transformers 向量模型，支持增量更新。

#### 类定义

##### KnowledgeBase

```python
class KnowledgeBase:
    """知识库管理类 - 支持增量更新"""
    
    def __init__(self, embeddings_provider: str = "local"):
        """
        初始化知识库
        
        Args:
            embeddings_provider (str): 向量化提供商 ("local" / "deepseek_api")
        
        Examples:
            >>> kb = KnowledgeBase()
            >>> kb = KnowledgeBase(embeddings_provider="local")
        """
    
    def load_documents_from_dir(self) -> int:
        """
        从文档目录加载所有文档（带增量更新检查）
        自动跳过未修改的文件（基于 mtime）
        
        Returns:
            int: 新增/更新的文档数
        
        Examples:
            >>> kb = KnowledgeBase()
            >>> updated = kb.load_documents_from_dir()
            >>> print(f"Updated: {updated}")
        """
    
    def search(self, query: str, top_k: int = 3) -> List[Dict]:
        """
        向量相似度搜索
        
        Args:
            query (str): 查询文本
            top_k (int): 返回结果数（默认 3）
        
        Returns:
            List[Dict]: 搜索结果，每项包含：
                - content: 文档块内容
                - source: 源文件路径
                - chunk_index: 块索引
                - score: 相似度分数 (0~1)
        
        Examples:
            >>> kb = KnowledgeBase()
            >>> results = kb.search("机器学习", top_k=3)
            >>> for r in results:
            ...     print(f"{r['source']}: {r['score']:.2f}")
        """
    
    def get_statistics(self) -> Dict:
        """
        获取知识库统计信息
        
        Returns:
            Dict: 包含 total_files, total_chunks, total_size_mb,
                  embeddings_dim, cache_size
        
        Examples:
            >>> kb = KnowledgeBase()
            >>> stats = kb.get_statistics()
            >>> print(stats['total_chunks'])
        """
    
    def rebuild_index(self):
        """
        重建索引 — 清空并重新加载所有文档
        
        Examples:
            >>> kb = KnowledgeBase()
            >>> kb.rebuild_index()
        """
```

#### 使用示例

```python
from src.knowledge_base import KnowledgeBase

# 初始化知识库
kb = KnowledgeBase(embeddings_provider="local")

# 加载文档（增量更新）
updated = kb.load_documents_from_dir()
print(f"加载 {updated} 个文档")

# 查看统计
stats = kb.get_statistics()
print(f"文件数: {stats['total_files']}, 块数: {stats['total_chunks']}")

# 搜索
results = kb.search("深度学习框架", top_k=3)
for i, r in enumerate(results, 1):
    print(f"{i}. [{r['score']:.2f}] {r['source']} — {r['content'][:80]}...")

# 重建索引
kb.rebuild_index()
```

---

### 6. config.py - RAG 配置（第四阶段新增）

#### 新增配置项

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `RAG_ENABLED` | bool | True | 是否默认启用 RAG |
| `RAG_TOP_K` | int | 3 | 每次检索返回的文档块数 |
| `RAG_SYSTEM_PROMPT_TEMPLATE` | str | (见源码) | 增强版 system prompt 模板 |
| `RAG_CONTEXT_ITEM_TEMPLATE` | str | (见源码) | 单条检索结果格式化模板 |

#### RAG 工作流

```
用户提问 → kb.search(query, top_k=3) → 格式化上下文 → 
注入 RAG_SYSTEM_PROMPT_TEMPLATE → LLM 生成回复
```

#### 自定义 RAG 模板

```python
# 在 config.py 中自定义 prompt 模板
RAG_SYSTEM_PROMPT_TEMPLATE = """{system_prompt}

## 参考知识库
{context}

请仅基于以上参考内容回答，如果无法回答请说明。"""

RAG_CONTEXT_ITEM_TEMPLATE = """[来源: {source} 相似度: {score:.2f}]
{content}"""
```

---

### 7. vector_store.py — 向量存储抽象（第五阶段新增）

#### 概述
向量存储抽象层，支持 Chroma (SQLite) 和 Faiss 两种后端，通过统一接口切换。

#### 类定义

##### VectorStore（抽象基类）

```python
class VectorStore(ABC):
    """向量存储抽象基类"""
    
    def add(self, ids, documents, metadatas, embeddings) -> None: ...
    def search(self, query_embedding, top_k) -> Dict: ...
    def delete(self, ids) -> None: ...
    def clear(self) -> None: ...
    def count(self) -> int: ...
```

##### ChromaStore / FaissStore

```python
from src.vector_store import create_vector_store

# Chroma（默认，SQLite 持久化）
store = create_vector_store("chroma")

# Faiss（内存索引，手动 save/load 持久化）
store = create_vector_store("faiss", dimension=384)
store.add(ids, documents, metadatas, embeddings)
store.save()  # 保存到 data/faiss_index.bin
```

配置：
```python
# config.py
VECTOR_STORE = "chroma"   # 或 "faiss"
FAISS_INDEX_PATH = "data/faiss_index.bin"
```

---

### 8. embeddings_manager.py — 多提供商（第五阶段重构）

#### 概述
支持本地模型（SentenceTransformer）和 OpenAI/兼容 API 两种向量化方式。缓存键使用 SHA256 避免 hash 碰撞。

#### 新增：OpenAI 提供商

```python
from src.embeddings_manager import EmbeddingsManager

# 本地模型（默认）
em = EmbeddingsManager("local")           # 384 维

# OpenAI API
em = EmbeddingsManager("openai")          # text-embedding-3-small, 1536 维
```

配置（`.env`）：
```bash
OPENAI_API_KEY=sk-xxx
OPENAI_API_BASE=https://api.openai.com/v1    # 也兼容第三方
OPENAI_EMBEDDINGS_MODEL=text-embedding-3-small
```

---

### 9. knowledge_base.py — 混合检索（第五阶段新增）

#### 概述
`hybrid_search()` 方法结合 BM25 关键词匹配和向量相似度，提升检索召回率。

#### 方法签名

```python
def hybrid_search(self, query: str, top_k: int = 3,
                  bm25_weight: float = None) -> List[Dict]:
    """
    BM25 + 向量混合检索
    
    融合公式: final = BM25_weight * BM25 + (1-BM25_weight) * Vector
    
    Returns:
        [{"content": ..., "source": ..., "score": 0.xxx,
          "vector_score": 0.xxx, "bm25_score": 0.xxx}, ...]
    """
```

#### 使用示例

```python
kb = KnowledgeBase(embeddings_provider="local")
kb.load_documents_from_dir()

# 混合检索
results = kb.hybrid_search("Python 基础语法", top_k=3)
for r in results:
    print(f"[{r['score']:.3f}] BM25={r['bm25_score']:.3f} Vec={r['vector_score']:.3f}")
```

配置：
```python
# config.py
HYBRID_SEARCH_ENABLED = True   # chat_with_rag 自动使用
BM25_WEIGHT = 0.3              # BM25 权重（0~1）
```

---

最后更新：2026-06-06
