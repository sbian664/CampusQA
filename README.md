# CampusQA 后端

CampusQA 的 FastAPI 服务负责聊天、检索、Agent Loop、会话存储、知识库维护与管理 API。用户侧小程序和维护者控制台是两个独立仓库；前端不包含向量索引或知识库运行数据。

## 仓库关系

| 仓库 | 职责 |
| --- | --- |
| `Agent-backend`（本仓库） | FastAPI、RAG/Agent、会话、知识库与管理 API |
| `CampusQA-admin` | Vue 管理控制台 |
| `开发小程序` | 原生微信小程序聊天端 |

前后端接口以本仓库当前代码生成的 `/openapi.json` 为准，接口概述见 [API_REFERENCE.md](API_REFERENCE.md)。管理端和小程序的测试与运行方式分别以各自 README 为准。

## 本地启动

Windows PowerShell 示例：

```powershell
cd D:\Projects\Agent-backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
if (!(Test-Path .env)) { Copy-Item .env.example .env }
python server.py
```

按需编辑 `.env`，填写模型服务密钥和本地管理员密码。不要提交 `.env` 或运行数据。`python server.py` 默认监听 `0.0.0.0:8000`；本机文档地址为 `http://localhost:8000/docs`，健康检查为 `/api/health`。服务启动时加载 embedding 模型和知识库，首次下载模型可能需要较长时间。

启动后端后，可在设置页把小程序地址切到 `http://localhost:8000`，也可按管理端 README 启动控制台。小程序默认配置和正式 request 域名要求见前端 README。

## 主要能力

- 同步聊天 `POST /api/chat`，以及可续读、可取消的后台流式聊天 `POST /api/chat/runs`。
- 会话读取、删除与消息编辑等兼容接口。
- 混合检索、可按请求启用的 CrossEncoder 重排，以及默认关闭的实验性上下文路由。
- 附件解析、文档投稿和知识库扫描/索引维护。
- 需要管理员 Session Cookie 和 CSRF 校验的 `/api/admin/*` 管理 API。

聊天任务和未完成事件保存在单个服务进程内；服务应以单个 Uvicorn worker 运行。维护任务、备份与恢复边界见 [OPERATIONS.md](OPERATIONS.md)。

## 常用文档

- [API_REFERENCE.md](API_REFERENCE.md)：接口分组、聊天流式协议、认证和状态码。
- [ADMIN_SETUP.md](ADMIN_SETUP.md)：控制台本地启动与管理接口边界。
- [MINIPROGRAM_COMPATIBILITY.md](MINIPROGRAM_COMPATIBILITY.md)：小程序客户端兼容核验。
- [OPERATIONS.md](OPERATIONS.md)：运行限制、索引维护、备份、恢复和部署注意事项。
- [VERIFICATION.md](VERIFICATION.md)：已执行的本地验证及其范围。

## 部署边界

本地开发、Git 推送和生产实例部署是不同操作。除非当前任务明确要求部署，否则只在本地修改与验证，不登录或同步生产实例。部署前必须重新核对目标提交、实例配置、备份和回滚方案；不要覆盖服务器上的 `.env`、模型或运行数据。
