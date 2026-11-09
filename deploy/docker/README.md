# CampusQA 离线包

目标为 Linux amd64、CPU、单进程。目标机需预装 Docker Engine、Compose、bash、tar、gzip 和 sha256sum；不需要 Python、Node、pip 或模型下载网络。包内不包含业务文档、会话及真实凭据。微信登录仍需访问微信服务，聊天使用云供应商时仍需供应商网络。这里的“离线”指镜像、Python/Node 运行依赖与本地检索模型可离线安装，不代表微信或云模型无需网络。

## 首装

1. 核对外部 `.sha256`，解压整个发行包。不要只取镜像而遗漏 models。
2. `bash scripts/install.sh /srv/campusqa`：校验全部文件、导入镜像并建立配置示例。首次预检会提示填写 Key、管理员初始密码、微信配置和个人模型加密主密钥，属于预期行为，不会启动服务。
3. 编辑 `/srv/campusqa/config/app.env`，填写 Key、管理员密码、WECHAT_APP_ID、WECHAT_APP_SECRET、USER_LLM_ENCRYPTION_KEY 和实际 HTTPS 来源。主密钥必须是 URL-safe base64 编码的 32 字节随机值；迁移已有个人模型配置时必须保留原主密钥，不能重新生成替换。不要将完成的文件传回发行目录。HTTP 本地验收才临时关闭 secure cookie。
4. 再运行安装预检，然后 `bash scripts/manage.sh /srv/campusqa up`。默认只暴露宿主机 `127.0.0.1:8000`，由现有 HTTPS 代理转发；可用 `CAMPUSQA_PORT=18000` 做隔离验收。

模型直接从版本化发行目录只读挂载，保留该目录直至不再使用本版。启动不会拉镜像或补下载模型。若文件校验失败，重新取得正确发行包，不要跳过校验。

管理脚本按安装根目录生成稳定的 Compose 实例名。同一安装目录升级时沿用原实例；试运行须使用另一安装目录、独立 data 和另一宿主机端口。不要手工省略实例名启动 Compose，统一使用 manage.sh。

## 迁移

停止旧服务并作一致性备份。将完整 data 备份复制到新环境 data，保持文件可由 UID 10001 写入；不要与旧服务共用同一个可写目录。先在副本演练。

此版本只支持 legacy-only 索引的路径迁移。若 data/cache/index_versions 中存在 managed 版本、活动/回退/在建版本指针、孤立目录或损坏的指针，dry-run 和 apply 都拒绝执行，且不修改元数据或维护状态。不要删除版本目录或 registry.json 绕过检查。新版本索引跨路径迁移须另行实现并在完整副本验证；当前工具不能完成这一步。

确认是 legacy-only 数据后，在启动应用之前运行：

```bash
bash scripts/manage.sh /srv/campusqa migrate --old-root /previous/app/data/documents
bash scripts/manage.sh /srv/campusqa migrate --old-root /previous/app/data/documents --apply
```

dry-run 检查缺文件、路径越界、映射冲突。执行保留 document_id、原元数据备份和迁移报告，先设 repair_required 再修改路径。它不自动重建、不修改会话正文、不改写历史追踪、不读取 Key。发现未解决的回滚备份时停止，先人工恢复。

迁移完成后启动应用，经管理员页面执行完整重建。原先处于维护的索引仅完整成功重建才解除维护；失败和重启仍保留维护。健康活动版本的在线影子重建失败则保留旧版本可用，不应强制关闭检索。不要直接删除 index_state.json 来开放流量。须完成真实检索与小程序兼容核验后才切换入口。

## 停止、备份、恢复和更新

```bash
bash scripts/manage.sh /srv/campusqa down
bash scripts/manage.sh /srv/campusqa backup --destination /backups/before-update
```

备份工具要求应用停止，并拒绝正在使用的数据。恢复到新的空 data 目录，使用 `restore --source /backups/before-update`；先完整保留原故障目录。宿主机 backups 目录也须允许 UID 10001 写入。全包更新不覆盖 app.env/data；验证新包后指向新发行目录并启动，模型清单不匹配或缺失则拒绝。应用更新可使用相同镜像与模型清单机制，只传变化产物；任何省略模型的包必须保留并校验完整所需模型清单，不得盲目复用未知模型。

回退必须使用匹配的旧镜像、控制台、配置与一致性数据备份。只换镜像不会撤销数据库、索引或新消息。维护窗口停写，回退前保留现场，避免丢失新数据。

## 验收与运行边界

`/api/health` 匿名可用；`/api/kb/stats` 及用户业务路由需要 Bearer Token，管理 API 使用独立 Cookie / CSRF。维护不是进程故障。容器的 unhealthy 标记不自动重启，restart 策略处理进程退出。默认只运行一个 worker；数据锁阻止第二实例同时写入。

临时目录 `/app/data/tmp` 持久化，正常附件按应用逻辑清理；`campusqa_rollback_*` 是失败恢复现场，禁止自动删除。日志进入 Docker，10 MB × 3 轮转。正常升级先等待任务结束；180 秒停止宽限不是重建完成保证。

新包必须在无外网环境验证模型加载、真实向量化、重排、索引重建和重启持久化。模型检验通过不代表云 API 问答质量或小程序真机已通过。镜像在 ARM 仿真测试的结果不能当作原生 amd64 性能结果。


## 本次适配与验收范围

后端基线 `359c240`，管理端基线 `0b161a0`；离线工具移植自 `255c178`，同源连接适配来自 `02335d1`。保留新版用户隔离、个人模型加密、在线索引版本和原生小程序协议。管理端生产构建未指定 API 地址时使用页面当前 origin，不会因缺少地址切换到演示数据。

`verify_image.py` 只创建独立 Docker volume 和 internal network，用虚构文档和临时双用户 Bearer Token 验证隔离。Token 通过容器内部的 UserStore 写入一次性测试数据库，不新增模拟登录 HTTP 接口。回环 LLM 只由管理员配置；用户 local 供应商仍拒绝。脚本覆盖匿名拒绝、跨用户会话拒绝、真实本地嵌入/重排、上传、在线重建失败保留活动版本、重启及停止后备份恢复。此脚本不代替真实微信登录、模型供应商质量、小程序真机或原生 amd64 性能验收。

运行镜像验收前须已具备 Docker daemon、linux/amd64 镜像和完整模型目录。不要把单元测试、Compose 配置校验或脚本编译通过表述为镜像验收成功。本次执行记录见配套的适配报告。

构建器在组装任何发行文件前校验镜像平台及 backend/admin 两个完整提交标签；`--skip-build` 也必须与请求提交完全一致，缺失或不匹配时拒绝组装。镜像 tag 本身不作为源码身份凭证。
