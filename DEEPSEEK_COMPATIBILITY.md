# DeepSeek 当前模型最小兼容修改

日期：2026-11-10。仅在隔离源码副本完成，没有部署或付费请求。

## 基线与变更
基线为 Mac 本地已验证发行源码 bb51f065b59762242ceb0255a76c49a00fcb97fc。云端先前取得的 e6840aa2 副本未修改，不是最终补丁基线。

- config.py 与 .env.example 的新配置默认模型改为 deepseek-flash。
- 官方 HTTPS api.deepseek.com 地址、deepseek 供应商，以及 deepseek-flash、deepseek-v4-pro、deepseek-v4-flash、deepseek-v4-flash-vision-exp 四个明确模型名称，统一发送 thinking.type=disabled。主机名大小写及末尾点按配置校验的等价规则处理。
- 非流式调用支持降低单次任务尝试次数；管理端连接测试仅尝试一次，仍最多输出 8 tokens。一般聊天的原有重试次数未改变。
- 管理端连接测试拒绝空、纯空白或非字符串正文；测试不保存配置。
- 不自动改写已保存的模型或显式 MODEL_NAME 环境设置。旧 deepseek-chat/deepseek-reasoner 需另行明确迁移，不做静默替换。custom 供应商及代理地址不自动添加 DeepSeek 专有参数。

## 离线验证
Python 3.13.12、pytest 9.1.1，复用已有 review-venv，没有安装依赖或下载模型。

- 修改前新增首批测试：15 失败、8 通过，复现所修问题。
- 最终新增测试共 32 项，包含默认配置隔离、显式配置保留、供应商和域名边界、正文/工具/流式请求、超时/429/503 仅一次尝试、空回复拒绝、不保存配置、正常聊天重试保持。
- 专项回归：50 通过。
- 全套后端回归：315 通过、19 子测试通过、45 条弃用警告，5.53 秒。警告为 langchain-community 维护状态及已有 Pydantic dict() 用法，未改动这些部分。
- 只读复核指出的测试环境依赖和主机名末尾点问题已修复，并重新运行全部测试。
- 测试运行器清空进程环境，关闭 dotenv，设置模型离线标志，并阻断 Python socket 连接和 DNS；使用合成 Key 与临时数据。没有调用真实微信、云模型或服务器。

## 保留的边界
这次结果证明源码级离线兼容与回归通过，不等于真实模型、账号、余额或小程序联调成功。没有重建镜像、替换发行包、切换公网入口或修改现网。

原工作树仍干净。旧发行包 SHA256 复核仍为 dd7c479fa223e1794ea3a5267ff713dc54ae1220bfa1e5258c393b2050063e2d。当前隔离副本约 8.2 MiB；Mac 约 7.4 GiB 可用。

后续先按审批配置隔离访问与凭据，执行一次非思考 Flash 探针；成功后再逐项验证真实微信登录及合成问答。实际凭据使用、测试数据接收方及费用范围仍需确认。不得把当前补丁或旧镜像视为已上线版本。

## 官方依据（2026-11-10 核实）
- 旧型号停用公告：https://api-docs.deepseek.com/news/news260424/
- 当前型号及价格：https://api-docs.deepseek.com/zh-cn/quick_start/pricing/
- 非思考参数：https://api-docs.deepseek.com/guides/thinking_mode/
