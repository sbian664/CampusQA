# 知识库 AI Agent（项目名：CampusQA）— v1.2.0

从基础对话框架逐步演进为知识库问答系统，现已支持 **Web 前端**（ChatGPT 风格）。

> v1.2.0 新增请求级 CrossEncoder 智能重排、前端持久化开关、多轮上下文路由，并提升知识库批量索引并发安全性。

智能搜索由前端按请求传递 `rerank_enabled`，不会修改服务器全局状态；关闭时保持原有混合检索排序。首次开启会懒加载 CrossEncoder 模型。

多轮对话默认启用上下文路由：首轮问题直接进入问答流程；后续问题由 `deepseek-chat` 判断为独立问题或追问。独立问题不携带旧历史，追问会被改写成可独立检索的问题，并只保留最近一轮相关对话。路由失败时会保守使用原问题和最近一轮历史，完整 Session 仍保存用户原始输入。

## 项目结构

```
knowledge-agent/
├── src/
│   ├── __init__.py
│   ├── llm_client.py          # LLM 客户端（支持多个提供商）
│   ├── context_router.py      # 多轮上下文分类、追问改写与历史选择
│   ├── chatbot.py             # 对话机器人（支持 RAG + Agent Loop）
│   ├── session.py             # 会话管理（支持 tool_calls）
│   ├── document_loader.py     # 多格式文档加载器
│   ├── embeddings_manager.py  # 向量化管理
│   ├── text_chunker.py         # 语义感知分块器
│   ├── knowledge_base.py      # 知识库（Chroma + 检索 + 混合检索 + 元数据过滤）
│   ├── reranker.py            # CrossEncoder 懒加载与请求级智能重排
│   ├── vector_store.py        # 向量存储抽象（Chroma / Faiss）
│   └── tools.py               # Agent 工具定义 + ToolHandler
├── frontend/                 # Vue 3 Web 前端（ChatGPT 风格）
│   ├── src/
│   │   ├── App.vue
│   │   ├── intelligentSearch.js    # 智能搜索开关本地持久化
│   │   └── components/
│   │       ├── ChatBubble.vue      # 气泡组件（marked + DOMPurify）
│   │       ├── ChatMessages.vue    # 消息列表 + 自动滚底
│   │       ├── ChatInput.vue       # auto-resize 输入框
│   │       └── ErrorToast.vue      # 错误提示
│   └── vite.config.js
├── data/
│   ├── documents/            # 知识文档存放目录
│   ├── cache/               # 缓存数据
│   └── kb.db/              # Chroma 向量数据库 (SQLite)
├── server.py               # FastAPI Web API 服务器
├── config.py                # 配置文件
├── main.py                 # 启动脚本 (CLI 交互)
├── requirements.txt        # Python 依赖
└── .env.example           # 环境变量示例
```

## 快速开始

### 1. 克隆/初始化项目

```bash
cd d:\Projects\Agent
```

### 2. 创建虚拟环境（可选）

```bash
python -m venv venv
venv\Scripts\activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

### 4. 配置环境变量

复制 `.env.example` 为 `.env`：

```bash
cp .env.example .env
```

编辑 `.env` 文件，填入你的 DeepSeek API 密钥：

```
DEEPSEEK_API_KEY=your_actual_key_here
```

### 5. 运行程序

**方式一：Web 界面（推荐）**

```bash
# 终端 1 — 启动后端 API
python server.py

# 终端 2 — 启动前端开发服务器
cd frontend
npm install
npm run dev
```

浏览器访问 `http://localhost:5173`，即可使用 ChatGPT 风格界面。

**方式二：命令行**

```bash
python main.py
```

### 6. 使用命令

程序启动后自动加载知识库，你可以：

**Agent 自主检索对话**（默认模式）— LLM 自主调用检索工具：
```
你: 什么是反向传播算法？
🤖 Agent 思考中（可自主检索）...
  🔍 search_knowledge_base("反向传播算法") → 3条, top=0.87
Agent: 根据《深度学习教程》文档，反向传播算法是...
```

**一步式 RAG 对话** — `/agent` 切换后可回到传统模式：
```
你: 什么是监督学习？
🔍 检索知识库 + 🤔 思考中...
Agent: 根据知识库文档，监督学习是...
```

**Agent 模式命令**：
```
你: /agent     # 切换 Agent/Simple 模式
你: /mode      # 查看当前对话模式
你: /tool-log  # 查看工具调用历史
你: /cost      # 查看 Token 消耗
```

**对话管理命令**：
```
你: /help      # 显示帮助
你: /history   # 查看对话历史
你: /summary   # 显示会话摘要
你: /save      # 保存会话
你: /load      # 加载会话
你: /clear     # 清空历史
你: /quit      # 退出程序
```

**知识库命令**：
```
你: /add-docs   # 扫描并加载新文档
你: /search 机器学习  # 搜索知识库
你: /search 机器学习 --type markdown --after 2026-01-01  # 元数据过滤搜索
你: /kb-stats   # 查看知识库统计
你: /rebuild    # 重建向量索引
```

## 实现路线图

- 第一阶段：基础对话框架 ✅
- 第二阶段：对话记忆管理 ✅
- 第三阶段：文档加载 ✅
- 第四阶段：文本检索 + RAG 增强 ✅
- 第五阶段：向量检索扩展（Faiss + API Embeddings + 混合检索） ✅
- 第六阶段：RAG检索强化（上下文增强 + 元数据过滤） ✅
- 第七阶段：Agent Loop 自主循环检索 ✅
- 第八阶段：Web 前端（Vue 3 + TailwindCSS + FastAPI） ✅
- 第九阶段：CrossEncoder 智能重排 + 并行知识库索引 ✅ — **v1.2.0**

## 获取 DeepSeek API 密钥

1. 访问 https://platform.deepseek.com
2. 注册/登录账户
3. 创建 API 密钥
4. 复制密钥到 `.env` 文件

## 故障排除

- **"DEEPSEEK_API_KEY 未设置"**：检查 `.env` 文件是否存在且配置正确
- **连接超时**：检查网络连接和 API 地址
- **API 错误**：确保 API 密钥有效且有余额

## 可选配置

项目通过 `config.py` 和环境变量 `.env` 集中管理配置。以下为所有可选项及其默认值：

### LLM 配置

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `LLM_PROVIDER` | `deepseek` | LLM 提供商：`deepseek` / `local` |
| `DEEPSEEK_API_KEY` | — | DeepSeek API 密钥 |
| `DEEPSEEK_API_BASE` | `https://api.deepseek.com/v1` | DeepSeek API 地址 |
| `MODEL_NAME` | `deepseek-chat` | 模型名称 |
| `MAX_TOKENS` | `4000` | 最大 Token 数 |
| `TEMPERATURE` | `0.7` | 生成温度参数 |

### 向量存储（Phase 5）

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `VECTOR_STORE` | `chroma` | 后端：`chroma`（SQLite）/ `faiss`（内存索引） |
| `EMBEDDINGS_PROVIDER` | `local` | 向量化：`local`（MiniLM）/ `openai` / `deepseek_api` |
| `OPENAI_API_KEY` | — | OpenAI Embeddings API 密钥 |
| `OPENAI_API_BASE` | `https://api.openai.com/v1` | OpenAI 兼容 API 地址 |
| `OPENAI_EMBEDDINGS_MODEL` | `text-embedding-3-small` | OpenAI Embeddings 模型 |
| `KB_INDEX_MAX_WORKERS` | `4` | 文档解析与向量计算的并行工作线程数；向量存储提交保持串行 |

### 检索增强（Phase 4/5/6）

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `RAG_ENABLED` | `True` | 是否默认启用 RAG |
| `RAG_TOP_K` | `3` | 每次检索返回的文档块数 |
| `HYBRID_SEARCH_ENABLED` | `True` | 是否启用 BM25+向量混合检索 |
| `BM25_WEIGHT` | `0.25` | BM25 权重（0~1），剩余为向量权重 |
| `CONTEXT_ENRICHMENT_ENABLED` | `True` | 嵌入前是否添加文档/章节上下文前缀 |
| `METADATA_FILTER_FIELDS` | `doc_type, source, mtime_after, mtime_before` | 可过滤元数据字段 |
| `RERANKER_AVAILABLE` | `True` | 是否允许聊天请求启用 CrossEncoder 智能重排 |
| `RERANKER_MODEL` | `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` | 重排模型（首次使用时懒加载） |
| `RERANKER_CANDIDATE_K` | `30` | 交给重排模型的混合检索候选数 |
| `RERANKER_BATCH_SIZE` | `16` | CrossEncoder 推理批量大小 |
| `RERANKER_MAX_LENGTH` | `512` | CrossEncoder 最大输入长度 |

### Agent Loop（Phase 7）

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `AGENT_MODE_ENABLED` | `True` | 是否默认启用 Agent 自主检索模式 |
| `AGENT_MAX_LLM_ROUNDS` | `5` | L1：最大 LLM 对话轮次 |
| `AGENT_MAX_TOTAL_TOOL_CALLS` | `10` | L2：累计工具调用上限 |
| `AGENT_DUPLICATE_THRESHOLD` | `0.85` | L3：重复查询 Jaccard 相似度阈值 |
| `AGENT_LOW_SCORE_THRESHOLD` | `0.4` | L5：低分熔断阈值 |
| `AGENT_CONTEXT_RATIO` | `0.8` | L6：Token 预算告警比例 |
| `AGENT_MODEL_MAX_CONTEXT` | `32000` | 模型上下文窗口大小 |

### 多轮上下文路由

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `CONTEXT_ROUTER_ENABLED` | `true` | 是否在非首轮请求进入 Chatbot 前执行上下文路由 |
| `CONTEXT_ROUTER_PROVIDER` | `deepseek` | 路由模型提供方；可设为现有的 `local` OpenAI-compatible 客户端 |
| `CONTEXT_ROUTER_HISTORY_EXCHANGES` | `2` | 提交给路由模型判断的最近完整问答轮数 |

路由模型使用低温度 JSON 输出，返回 `standalone` 或 `follow_up` 以及独立改写后的问题。切换本地模型时可在 `.env` 中配置：

```bash
CONTEXT_ROUTER_PROVIDER=local
LOCAL_MODEL_BASE=http://localhost:8000/v1
LOCAL_MODEL_NAME=your-local-instruct-model
```

### 文本分割

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `CHUNK_SIZE` | `500` | 每个文档块的 Token 数 |
| `CHUNK_OVERLAP` | `50` | 块间重叠 Token 数 |

### 切换示例

**使用 Faiss + OpenAI Embeddings：**

```bash
# .env 文件
VECTOR_STORE=faiss
EMBEDDINGS_PROVIDER=openai
OPENAI_API_KEY=sk-your-openai-key
```

无需修改代码，重启程序即可生效。
