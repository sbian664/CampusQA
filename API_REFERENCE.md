# CampusQA API 参考

本文件概述当前 FastAPI 对外接口。实际请求模型、参数类型和响应结构以同一后端版本的 `/openapi.json` 为准；管理控制台需使用管理员会话的接口见 [ADMIN_SETUP.md](ADMIN_SETUP.md)，索引维护流程见 [OPERATIONS.md](OPERATIONS.md)。

本地启动：在后端仓库根目录执行 `python server.py`，默认监听 8000 端口；本机访问 `http://localhost:8000`，Swagger UI 为 `/docs`。

## 用户接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/health` | 服务与知识库健康状态 |
| `POST` | `/api/chat` | 同步聊天接口，供不支持分块响应的客户端兼容使用 |
| `POST` | `/api/chat/runs` | 创建后台聊天任务，成功返回 `202` |
| `GET` | `/api/chat/runs/{run_id}` | 查询任务状态、当前文本快照或最终结果 |
| `GET` | `/api/chat/runs/{run_id}/events?after={sequence}` | 通过 SSE 订阅并续读任务事件 |
| `POST` | `/api/chat/runs/{run_id}/cancel` | 请求取消任务 |
| `GET` | `/api/sessions` | 列出会话 |
| `GET` | `/api/session/{session_id}` | 读取会话历史 |
| `DELETE` | `/api/session/{session_id}` | 清空指定会话历史 |
| `DELETE` | `/api/sessions/{session_id}` | 删除指定会话 |
| `DELETE` | `/api/session/{session_id}/message/{index}` | 删除会话中的一条消息 |
| `POST` | `/api/attachments/parse` | 解析单次聊天附件，不加入知识库 |
| `POST` | `/api/submissions` | 提交知识文档供管理员审核 |
| `GET` | `/api/submissions/{submission_id}/status` | 查询投稿处理状态 |

聊天请求的主要字段如下：

```json
{
  "message": "课程退选规则是什么？",
  "session_id": null,
  "rerank_enabled": true,
  "context_router_enabled": false,
  "reroll": false,
  "edit_index": null
}
```

新会话由服务端分配 UUID。聊天成功响应包含 `response`、`session_id`、`session_title`、`finish_reason`、`tool_calls`、`usage`、`rounds` 和实际执行模式 `effective_mode` 等字段；以 OpenAPI 模型为准。

## 流式聊天任务

客户端先向 `POST /api/chat/runs` 发送聊天字段及 UUID `run_id`。重复提交相同 ID 和相同请求可安全恢复；以同一个 ID 提交不同请求返回冲突。然后连接事件接口，从 `after` 序号续读。断开 SSE 连接不会停止生成；只有调用取消接口才会请求停止。

事件类型包括 `status`、`delta`、`snapshot`、`done`、`error` 和 `cancelled`。客户端应按事件序号增量应用文本，并在断线或回到前台时先查询任务再继续订阅。任务终态包括 `succeeded`、`cancelled`、`error` 和 `interrupted`。任务元数据保留 24 小时，半成品文本仅在服务进程运行期间暂存；聊天任务、会话锁和未完成事件要求单个 Uvicorn worker。

微信小程序仅在基础库支持 `RequestTask.onChunkReceived` 时使用 `wx.request` 分块订阅；不支持时改用同步 `/api/chat`。任务创建后不能因订阅失败而重新发起一个新任务。

## 知识库与配置接口

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| `GET` | `/api/kb/stats` | 知识库统计与索引状态 |
| `POST` | `/api/kb/search` | 用户侧知识检索 |
| `POST` | `/api/kb/scan` | 兼容管理入口；需要管理员登录和 CSRF |
| `POST` | `/api/kb/rebuild` | 兼容管理入口；需要管理员登录和 CSRF |
| `POST` | `/api/upload` | 兼容管理入口；需要管理员登录和 CSRF |
| `POST` | `/api/upload-legacy` | 旧版上传兼容入口；需要管理员登录和 CSRF |
| `GET` / `PUT` | `/api/llm-config` | 用户侧模型配置读写 |
| `POST` | `/api/llm-config/test` | 测试用户侧模型配置 |
| `GET` | `/api/mode` | 读取问答模式 |
| `POST` | `/api/mode/toggle` | 切换问答模式 |
| `GET` | `/api/cost/{session_id}` | 读取会话用量摘要 |
| `GET` | `/api/session/{session_id}/tool-log` | 读取工具调用记录 |
| `POST` | `/api/session/load` | 兼容会话加载入口 |

搜索、扫描和上传的筛选字段及返回模型以 `/openapi.json` 为准。新控制台使用的文档管理、索引任务、投稿审核、运行设置和审计接口统一位于 `/api/admin/*`。旧版管理兼容路由仍受管理员登录和 CSRF 保护；不要将用户接口误当成管理接口。

## 管理接口认证

管理 API 位于 `/api/admin/*`，主要包括认证、运行概览、会话和追踪、知识库检索/文档/任务、模型与运行配置、投稿审核、错误和审计记录。登录后使用 HttpOnly Session Cookie；写操作同时校验 `X-CSRF-Token`。控制台通过 `credentials: include` 发送 Cookie。初始化与本地启动步骤见 [ADMIN_SETUP.md](ADMIN_SETUP.md)。

## 状态码与诊断

- `400`：请求参数无效。
- `401` / `403`：管理员会话缺失或 CSRF 校验失败（管理接口）。
- `409`：会话/任务冲突，或当前状态不接受该写操作。
- `413`：文件超过 20 MiB 单文件限制。
- `429`：登录或投稿频率限制。
- `503`：知识库维护或服务暂不可用。

聊天响应及可处理错误包含 `X-Request-ID`。日志以请求 ID 关联聊天结果和失败阶段；不要在日志或客户端提示中暴露 API Key、附件正文或其他凭证。超时只表示客户端未在时限内确认结果，不代表服务端已取消操作；写请求超时后应查询状态，不要盲目重试。

## 版本约定

该仓库不以本文件中的手写版本号描述接口版本。接口能力随当前代码提交发布；核对具体字段时优先读取对应提交的 `/openapi.json`。
