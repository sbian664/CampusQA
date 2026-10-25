# 用户身份、个人模型与发布切换

有效微信身份即可使用，无邀请码或审批。知识库共享；会话、消息、用量、工具日志及投稿按用户隔离。管理员沿用独立 Cookie / CSRF，可查看所有记录。历史未归属数据保留，不自动分配。

## 环境与持久化

安装 `requirements.txt`（增加 `cryptography`），在后端环境设置 `WECHAT_APP_ID`、`WECHAT_APP_SECRET`、`USER_LLM_ENCRYPTION_KEY`。模型地址继续受 `LLM_CONFIG_ALLOWED_HOSTS` 白名单和 HTTPS 443 限制。

生成主密钥：`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`。直接存入环境配置并单独备份，不写入代码、聊天记录或 Git。主密钥缺失不影响服务器模型；个人配置保存或解密明确失败。不要生成新密钥覆盖旧密钥来恢复已有密文。

用户、Token 哈希、加密个人配置存入现有 `data/admin_control.db` 的 `app_users`、`user_tokens`、`user_llm_configs` 表。投稿表添加可空 `owner_user_id`；JSON 会话添加顶层不可变归属；既有流式任务 SQLite 添加同名字段。初始化可重复执行，旧归属保持为空。

`data/user_llm_config.json` 保留为服务器配置，仅由管理员管理，不复制给用户。本轮保留 JSON 会话存储，没有修改检索算法或新增流式协议；已有流式接口补齐权限校验。

## 登录和权限

1. 小程序调用 `wx.login`，向 `POST /api/auth/wechat` 提交 `{"code":"..."}`。
2. 后端向微信验证，按 `(AppID, OpenID)` 获取稳定内部 `user_id`，不接受客户端指定身份。
3. 返回 `user_id`、随机 `token`、`token_type=Bearer`、Unix 秒数 `expires_at`。有效期 7 天，数据库仅保存 Token 的 SHA-256 哈希。
4. 普通请求、上传、SSE 均携带 `Authorization: Bearer <token>`。`GET /api/auth/me` 返回当前编号；`POST /api/auth/logout` 撤销当前 Token。

除健康检查、微信登录及独立管理员入口外，所有 `/api/*` 业务接口需要登录。无效、过期、撤销 Token 返回 `401`；微信配置缺失为 `503`，验证拒绝为 `401`，微信服务异常/超时为 `502`。没有匿名兼容或模拟身份登录入口。

新会话不传 `session_id`，由服务器生成 ID。指定 ID 的续聊、编辑、重答、读取、清空、删除、用量、工具日志和加载，在会话锁中校验归属。不存在、他人所有、历史未归属均为 `404`。后台任务创建、查询、订阅、恢复、取消同样校验持久化归属。`POST /api/mode/toggle` 现在要求管理员 Cookie 和 CSRF；智能搜索和实验性路由保留请求级行为。

## 个人模型与投稿 API

`GET /api/llm-config` 返回当前用户的 `source`、`provider`、`model`、`base_url`、`has_api_key`。无个人配置时 `source=server`，个人字段为空，不返回服务器凭据。响应不含 Key 或掩码前后缀。

向 `PUT /api/llm-config` 保存：

```json
{
  "source": "personal",
  "provider": "deepseek",
  "model": "deepseek-chat",
  "base_url": "https://api.deepseek.com/v1",
  "api_key": "<个人 Key>"
}
```

`POST /api/llm-config/test` 测试相同字段但不保存。Key 留空仅复用当前用户、相同供应商及相同归一化 Base URL 的已存 Key；更换任意一项必须重新填写。`local` 仅允许管理员作为服务器模型使用；个人请求不跟随重定向。

发送 `{"source":"server"}` 切回服务器并保留个人配置；`DELETE /api/llm-config` 删除个人配置并切回服务器。每次聊天固定配置快照，正文、Agent 决策、已启用的路由及标题使用同一份。个人模式失败不自动回退，保存仅影响后续请求。

`GET /api/submissions?limit=50&offset=0` 返回当前用户的 `items/total/limit/offset`。单条状态也校验归属，旧 `X-Submission-Token` 不能代替登录。限额按用户统计，沿用原数量/时间窗口；重复文件检查按用户分区。审核通过后仍进入共享知识库。

## 小程序与控制台

冷启动先验证微信身份，再恢复私人缓存；并发页面共用登录。Token、当前会话、标题覆盖及后台任务缓存按后端地址、AppID、用户编号分区。旧匿名缓存不认领；账号/后端切换清空私人视图，并使旧请求和文件选择器回调失效。

遇 `401` 重新验证：只读请求最多重试一次，写操作保留输入并提示再次提交。个人 Key 不回填、不存入小程序；测试、保存、离开页面时清空输入。投稿页从后端加载自己的历史。控制台详情显示用户编号，空归属显示“历史未归属”。

## 配套切换、备份与回退

1. 暂停用户写入，记录三仓提交及环境配置。备份完整 `data/`，包括会话 JSON、管理/投稿 SQLite、流式任务 SQLite、知识库和投稿文件。SQLite 关闭连接后复制，或使用 backup API。
2. 单独备份 `.env`、服务器模型配置及 Fernet 主密钥，确认备份可读且不进入 Git。
3. 配置微信凭据及加密密钥，安装依赖，保持单个 Uvicorn worker。在测试环境核对重复升级、A/B 隔离及管理员查看旧数据。
4. 后端与小程序配套发布，并配置 HTTPS request 合法域名。旧匿名客户端会收到 `401`，没有匿名过渡开关；保留匹配的控制台构建。
5. 放行前用两名体验成员完成真实登录、上传、冷启动、账号切换、个人模型及任务恢复联调。

回退时先关闭用户入口并备份切换后的数据。旧后端没有隔离，不能直接读取含新用户私有数据的目录后重新开放。须隔离新数据，恢复切换前完整快照及配套客户端，或继续限制访问直至修复。不要只回退代码并开放旧匿名接口，也不要删除、认领历史数据来规避问题。

当前仅本地工作树变更，未合并、推送、部署或替换离线安装包。真实微信登录和两名体验成员真机联调尚未验收。

参考：[微信官方登录流程](https://developers.weixin.qq.com/miniprogram/dev/framework/open-ability/login.html)、[Fernet 文档](https://cryptography.io/en/latest/fernet/)。
