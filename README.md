# 知识库 AI Agent

从基础对话框架逐步演进为知识库问答系统。

## 项目结构

```
knowledge-agent/
├── src/
│   ├── __init__.py
│   ├── llm_client.py          # LLM 客户端（支持多个提供商）
│   ├── chatbot.py             # 对话机器人（支持 RAG）
│   ├── session.py             # 会话管理
│   ├── document_loader.py     # 多格式文档加载器
│   ├── embeddings_manager.py  # 向量化管理
│   └── knowledge_base.py      # 知识库（Chroma + 检索）
├── data/
│   ├── documents/            # 知识文档存放目录
│   ├── cache/               # 缓存数据
│   └── kb.db/              # Chroma 向量数据库 (SQLite)
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

```bash
python main.py
```

### 6. 使用命令

程序启动后自动加载知识库，你可以：

**RAG 增强对话** - 直接输入消息（自动检索知识库）：
```
你: 什么是监督学习？
🔍 检索知识库 + 🤔 思考中...
Agent: 根据知识库文档，监督学习是...
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
你: /kb-stats   # 查看知识库统计
你: /rebuild    # 重建向量索引
```

## 下一步计划

- 第一阶段：基础对话框架 ✅
- 第二阶段：对话记忆管理 ✅
- 第三阶段：文档加载 ✅
- 第四阶段：文本检索 + RAG 增强 ✅
- 第五阶段：向量检索优化（待评估）

## 获取 DeepSeek API 密钥

1. 访问 https://platform.deepseek.com
2. 注册/登录账户
3. 创建 API 密钥
4. 复制密钥到 `.env` 文件

## 故障排除

- **"DEEPSEEK_API_KEY 未设置"**：检查 `.env` 文件是否存在且配置正确
- **连接超时**：检查网络连接和 API 地址
- **API 错误**：确保 API 密钥有效且有余额
