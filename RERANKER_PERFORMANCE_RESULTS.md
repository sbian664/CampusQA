# 2026-11-12 本地重排性能验证

## 结论

功能实现和离线导出已验证，但本地 M4 上 O2 INT8 仍未快于 PyTorch FP32。因此保留 `RERANKER_BACKEND=torch` 默认值，将 ONNX 作为明确可选后端；没有将本地数值换算成 ECS 的线上延迟，也没有修改线上模型或部署服务器。

## 实验条件与限制

- Apple M4 / 16 GB Mac，CPU 推理，非独占桌面；不是 2c2g ECS。
- 已有本地 `cross-encoder/mmarco-mMiniLMv2-L12-H384-v1`（L12），不是线上所述 L6 checkpoint。
- 使用仓库真实文档经现有 SemanticChunker 分块，固定 6 个中英文查询、每个 30 个候选；每配置重复 3 轮，共 18 个样本。候选由固定种子选择，不是线上召回快照，没有人工相关性标注。
- batch_size=16、max_length=512 不变；模型已加载，逐波清空分数缓存；模型加载不计入下面的请求时间。P95 仅为这 18 个观测值的描述，不能视为生产尾延迟预测。
- 输入 pair 原始 token 长度中位数 206，P95 886.45，最大 1030；20.56% 超过原有 512 上限，按同一策略截断。本轮没有进一步缩短输入。
- 实测依赖：Python 3.13.12、torch 2.15.0、Sentence Transformers 5.7.0、Transformers 4.57.6、ORT 1.31.0、Optimum-ONNX 0.1.0。

## 单请求：模型热、分数缓存冷

| 配置 | P50（秒） | P95（秒） | 进程峰值 RSS（MiB） |
| --- | ---: | ---: | ---: |
| PyTorch FP32 / 2 threads | 1.204 | 1.395 | 1263 |
| ONNX O2 FP32 / 2 threads | 2.221 | 3.568 | 1205 |
| ONNX O2 INT8 / 2 threads | 1.662 | 1.966 | 1283 |
| ONNX O2 INT8 / 1 thread | 2.353 | 3.234 | 1042 |

每配置均实际执行 540 个 pair 打分、18 次 predict，缓存命中数为 0。图文件由 FP32 470,787,224 bytes 变为 INT8 118,143,613 bytes（约 25.1%）；但本次独立进程的峰值 RSS 并没有同比缩小。这些 RSS 包括导入与初始化峰值，不等于完整服务稳定内存；完整服务还驻留 embedding、知识索引等，2GB 实例仍须监测。

更早的未做 O2 融合的探索运行：ONNX FP32 P50 2.100 秒、INT8 P50 2.063 秒；O2 改善了本例 INT8 时间，但不足以超过本例 PyTorch。未优化的单线程探索曾与导出任务重叠，不作为正式线程对照。上述表格也不是受控独占服务器实验，不据此断言其他 CPU 上的后端优劣。

## 数值与排序回归

对同一个 180-pair fixture：O2 FP32 的最大分数差约 1.05e-5，6 个查询的 Top-1 和 Top-3 集合均与 PyTorch 相同。
O2 INT8 最大分数差约 0.264；Top-1 一致 5/6，6 个查询的 Top-3 集合均相同（集合相同不表示内部顺序不变）。这是与 FP32 的一致性，不是相关性/答案正确率证明。量化仍需在线上同 checkpoint、同真实候选集上验收，尤其是多条件与集合问题。

## 缓存与并发单列

完全预热分数缓存后，重排函数 P50 0.172 ms、P95 0.295 ms，540 个 pair 全部命中，0 次模型 predict；这是缓存收益，不是量化吞吐，也不是包含召回/LLM 的整轮问答延迟。

另做真实模型并发用例：4 个同时到达的相同 query+30 个候选，重复 3 波，波间清缓存：

| 项目 | 实测 |
| --- | ---: |
| 用户请求总数 | 12 |
| 请求需要的逻辑 pair 数 | 360 |
| 实际模型预测 pair 数 | 90 |
| 等待后缓存命中 | 270 |
| 实际 predict 调用次数 | 3 |
| 每请求 P50 / P95 | 1.123 / 1.124 秒 |

每波只预测一次，另外三个请求在排队后复用；这个刻意构造的相同请求负载减少 75% 重复 pair 计算。不同查询/不同内容不会享有相同幅度，不承诺总体线上加速 4 倍。并发单位测试另外覆盖队列满、超时、异常释放、命中绕过队列与 ContextVar 指标隔离。

## 工程验证与范围

- 原测试环境与独立 ONNX 环境：各 365 tests passed，19 subtests passed（45 warnings）。本轮新增 23 项测试。
- 原 FP32 模型文件未改写；新图与原始 JSON 报告保存在 Mac 的 `work/rerank-performance-20261112/`，没有将大模型或运行数据提交 Git。
- 新依赖在独立 `work/rerank-onnx-venv/` 中验证；原 `review-venv` 未升级。
- Linux x86_64 / Python 3.13 对应的 ONNX 1.23.2、ORT 1.31.0 wheels 已经 dry-run 验证（manylinux_2_28）；这不是完整 Docker 构建验收。
- 未在 ECS 执行基准；未构建新的生产 Docker 镜像；未部署/重启生产服务。上线及回退操作见 [RERANKER_PERFORMANCE.md](RERANKER_PERFORMANCE.md)。

## 原始报告定位

`work/rerank-performance-20261112/` 中的 `torch-t2.json`、`O2-fp32-t2.json`、`O2-int8-t2.json`、`O2-int8-t1.json`、`O2-int8-warm.json` 和 `same-query-clients4.json` 保留逐请求耗时、硬件/依赖、fixture 哈希、各候选分数/排序和指标。`cases.jsonl` 可重复使用；也可用 `scripts/build_rerank_smoke_fixture.py` 从相同版本的文档再生成。
