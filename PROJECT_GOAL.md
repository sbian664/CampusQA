# Project Goal

## Embedding model local reuse

- 目标：服务启动时优先复用项目本地的 embedding 模型，避免每次启动都访问或下载 Hugging Face 模型。
- 边界：只调整本地 embedding 模型的定位、一次性下载和启动加载；不改变 embedding 模型名称、向量缓存格式或其他检索逻辑。
- 验收：本地模型目录完整时不触发下载，并使用 `local_files_only=True`；目录不存在或不完整时下载到项目本地目录，下载完成后再使用本地目录加载。
