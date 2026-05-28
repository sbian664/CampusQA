# API 参考文档

## 📚 总览

本文档详细说明知识库 Agent 的核心API接口、使用方法和示例。

---

## 🔧 核心模块

### 1. llm_client.py - LLM 客户端

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

### 基本对话

```python
from src.chatbot import Chatbot

# 初始化
chatbot = Chatbot()

# 对话
response = chatbot.chat("你好")
print(response)
```

### 命令行交互

```bash
# 运行
python main.py

# 输入问题
你: 什么是AI?

# 退出
你: exit
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

A: 调整 `MAX_TOKENS` 参数或使用第二阶段的对话历史管理功能。

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

### 第二阶段 - Session（对话记忆）

```python
from src.session import Session

session = Session()
session.add_message("user", "第一个问题")
session.add_message("assistant", "第一个回答")
response = chatbot.chat_with_history(user_input, session.get_history())
```

### 第三阶段 - DocumentLoader（文档加载）

```python
from src.document_loader import DocumentLoader

loader = DocumentLoader("data/documents")
docs = loader.load_all()
```

### 第四阶段 - Retriever（文本检索）

```python
from src.retriever import Retriever

retriever = Retriever(knowledge_base)
relevant_docs = retriever.retrieve("查询内容", top_k=3)
```

---

最后更新：2026-05-29
