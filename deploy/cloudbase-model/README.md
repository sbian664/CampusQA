# CampusQA 云开发模型服务

小程序通过现有聊天接口发起问答。FastAPI 检索知识库、执行模型要求的工具调用并保存会话，云托管服务在报名环境中通过 CloudBase Node SDK 调用模型。云托管服务向 FastAPI 返回 Chat Completions JSON 或 SSE。

## 云托管配置

1. 在领取成长计划额度的云开发环境中创建容器型云托管服务。以本目录为构建目录，使用 `Dockerfile`，容器端口为 `8080`。
2. 配置 `CLOUDBASE_ENV_ID` 为领券环境的 ID。容器启动时检查云托管注入的 `CBR_ENV_ID` 是否与它一致。SDK 通过云托管的运行环境凭证鉴权。
3. 生成一个独立的随机密钥并设为 `CAMPUSQA_BRIDGE_TOKEN`。这个密钥只配置在云托管和 FastAPI 服务端。`CLOUDBASE_MODEL` 默认为 `hy4`，须与 FastAPI 的模型名称一致。`hy4`、`hy4-flash` 和 `hy3` 使用关闭深度思考的模式以兼容现有工具调用历史。
4. 配置 FastAPI 可访问的 HTTPS 地址。云托管的 `GET /healthz` 可用于探活，`POST /v1/chat/completions` 要求 `Authorization: Bearer <CAMPUSQA_BRIDGE_TOKEN>`。

## FastAPI 配置

对接前确认聊天接口只向授权的小程序用户开放。若部署的是无用户鉴权的测试后端，先限制访问范围。兼容模型配置接口 `/api/llm-config` 和 `/api/llm-config/test` 也应限制为可信操作方可访问。

1. 把云托管 HTTPS 域名加到 FastAPI 环境中的 `LLM_CONFIG_ALLOWED_HOSTS`，再重启 FastAPI 以加载允许列表。
2. 在管理端模型设置将 Provider 填为 `custom`。Base URL 填 `https://<云托管域名>/v1`，模型填 `hy4`，API Key 填 `CAMPUSQA_BRIDGE_TOKEN` 的值。可通过管理员接口 `POST /api/admin/llm-config/test` 测试连接。
3. 保存配置后，从小程序发起一次包含知识库检索的问答。检查流式回答、引用和会话历史，并在报名环境的 **AI 模型用量**核对活动包抵扣。

本服务使用 `cloudbase` 模型分组，要求资源点套餐。在允许的调用来源上优先抵扣活动额度，活动额度不足时可按环境套餐扣除资源点。可抵扣模型和适用条件见 [小程序成长计划使用指南](https://docs.cloudbase.net/ai/ai-inspire-plan-guide)。

## 本地测试

使用 Node.js 22，在本目录运行以下命令。测试通过本地 HTTP 调用模拟模型响应。

```powershell
npm ci
npm test
```
