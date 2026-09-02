# CampusQA Admin 管理后台

管理 API 挂载在 `/api/admin/*`，使用 HttpOnly Session Cookie 和 CSRF Token。运行时状态、任务、审计和管理员账号保存在 `data/admin_control.db`，该文件不应提交到 Git。

## 本地启动

1. 复制 `.env.example` 的管理配置到本地 `.env`，设置 `ADMIN_INITIAL_PASSWORD`。首次启动会将密码哈希写入 SQLite；不要把本地 `.env` 提交到仓库。
2. 启动后端 `uvicorn server:app --host 0.0.0.0 --port 8000`。
3. 在控制台项目复制 `CampusQA-admin/.env.example` 为 `.env.local`，确认 `VITE_API_BASE_URL=http://localhost:8000` 且 `VITE_DEMO_MODE=false`。
4. 启动前端 `npm run dev`，打开 `http://localhost:5173/#/login`。

生产环境必须使用 HTTPS、`ADMIN_COOKIE_SECURE=true`，并将 `ADMIN_CORS_ORIGINS` 设置为实际控制台来源。生产部署前还需要完成备份、回滚和真实 API 验收。

## 管理接口边界

配置、知识库上传/扫描/重建、会话删除和模式切换等旧兼容入口也要求管理员 Session 与 CSRF；用户聊天 `/api/chat` 和附件解析 `/api/attachments/parse` 不要求管理员登录。检索追踪只对新产生的 Agent 工具调用保存结构化证据，历史会话不会被虚构回填。
