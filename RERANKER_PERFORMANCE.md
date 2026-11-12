# 重排 CPU 性能：实现、验证与上线步骤

基线：`90fea91`（已有 pair 打分缓存及整轮重排问题复用）。本轮不减少候选、不缩短输入、不改融合公式、不更换重排模型；默认仍为 30 candidates / batch 16 / max_length 512。

## 1. 本轮实现

| 项目 | 行为 |
| --- | --- |
| 可选 ONNX 后端 | `RERANKER_BACKEND=torch/onnx`；ONNX 必须加载已存在的本地文件，`export=False`、`local_files_only=True`、CPUExecutionProvider。普通安装仍可只用 PyTorch。 |
| 离线 FP32 → O2 → INT8 | `scripts/export_reranker_onnx.py`；源模型不变，新目录原子落盘；O2 不启用 O3 的近似 GELU；包含哈希清单和烟雾预测校验。 |
| 线程预算 | embedding 与 reranker 共用进程级 PyTorch intra-op=2 / inter-op=1；ONNX intra-op=2、顺序执行、关闭线程自旋。可配置，需重启。 |
| 有界重排并发 | 默认每进程一次 predict，最多 8 个等待请求、等待上限 30 秒；缓存命中不排队；取得执行名额后再次查缓存。 |
| 并发重复计算 | 默认一个执行名额时，同一 pair 的重叠请求等待首个结果后复用；调大执行名额后不能承诺所有重叠 miss 都被合并。 |
| 失败处理 | 队列满/等待超时沿用已有检索回退，trace 明确记录原因；模型加载失败有 30 秒冷却，不反复加载。不会悄悄将配置的 ONNX 改成 FP32。 |
| 可观测性 | Agent 工具日志新增 `rerank_metrics`：总 pair、唯一 pair、命中、等待后命中、实际预测 pair/次数、queue_ms、predict_ms、model_load_ms、model_wait_ms、rerank_ms、backend、fallback_reason。 |
| 离线部署 | Docker 包含经验证的可选 ONNX 依赖；模型包可只含 ONNX 重排权重；启动检查选定 graph 是否在校验清单内；验收脚本检查真正的 rerank_score，避免把回退误认为成功。 |

`predict_ms` 包括 tokenizer 与模型前向计算，不是仅算子时间。队列超时只结束等待，不会强杀已经运行的原生推理。日志中的命中数按唯一 pair 计；初始重排与 BM25 补救的指标合并在同一次工具日志中。纯重排 benchmark 不包含 BM25/向量召回或 LLM。

本轮只有一个开发副本：`work/offline-validation-20261109/backend`，上游已明确设为 `github/codex/user-isolation`；`origin` 仍保留原有本地来源，不用它推送 GitHub。

## 2. 可选依赖与导出

推荐使用独立 Python 3.13 环境，避免在生产机上边运行服务边升级依赖：

```bash
python3.13 -m venv .venv-onnx
. .venv-onnx/bin/activate
# Linux CPU 环境先安装 CPU-only PyTorch（Mac 使用常规 torch 安装即可）
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-onnx.txt
pip check
```

`requirements-onnx.txt` 固定已验证的 Sentence Transformers 5.7.0、Transformers 4.57.6、Optimum-ONNX 0.1.0、ONNX 1.23.2、ORT 1.31.0。原 PyTorch 环境的 6.x 依赖不需要为了本地验证而被覆盖；这不是“任意最新版混装可用”的承诺。

导出使用**线上同一个 checkpoint**，不是换成另一个 MiniLM：

```bash
python scripts/export_reranker_onnx.py \
  --model-path /absolute/path/to/current-reranker \
  --output /absolute/path/to/reranker-onnx-avx2 \
  --profile avx2 --threads 2
```

profile 按目标 CPU 选 `avx2` / `avx512` / `avx512_vnni` / `arm64`；构建机与目标机可以不同，不应因为在 Mac 上导出就给 ECS 选 arm64。先检查目标机 CPU 指令集。输出保留原始 FP32 ONNX、O2 FP32 ONNX、INT8 graph、tokenizer/config 和 `reranker_export.json`。导出峰值内存可能高于在线推理，应在开发机/构建环境完成，不建议直接在 2GB 生产实例上导出。

O2 融合后的部分中间节点缺少类型标注：导出器使用 ORT 的 `DefaultTensorType=FLOAT`，对应本流程明确的 FP32 图，再按目标 profile 动态量化。这不适用于任意 FP16/多模态模型；本轮验证对象是 FP32 encoder-only CrossEncoder。

烟雾检查只证明文件可加载、FP32 导出数值基本一致、INT8 输出有效，不证明真实问答质量。动态 INT8 的尺度也可能受 batch 组合影响；需要同时检查原候选/补救候选的排序变化，不能把缓存的近似分数当作完全精确的概率。

## 3. 固定输入的性能与回归测试

先生成用于计时的固定校园文档样例（6 个查询，每个 30 个候选；不是人工相关性标签）：

```bash
python scripts/build_rerank_smoke_fixture.py --output data/cache/rerank-smoke.jsonl
```

真正回归集应使用实际 query–chunk 对。JSONL 每行格式：

```json
{"id":"q1","query":"完整的实际重排问题","candidates":[{"id":"doc1:0","content":"候选正文"}],"relevant_ids":["doc1:0"]}
```

`relevant_ids` 可省略；只有人工确认后才填写，不要把初检排序当作标签。所有后端必须使用同一个文件（报告会检查 fixture SHA256）。

```bash
python scripts/benchmark_reranker.py --cases data/cache/rerank-smoke.jsonl \
  --model-path /absolute/path/to/current-reranker --backend torch \
  --threads 2 --rounds 5 --output data/cache/rerank-torch.json

python scripts/benchmark_reranker.py --cases data/cache/rerank-smoke.jsonl \
  --model-path /absolute/path/to/reranker-onnx-avx2 --backend onnx \
  --onnx-file onnx/model_qint8_avx2.onnx --threads 2 --rounds 5 \
  --baseline data/cache/rerank-torch.json --output data/cache/rerank-int8.json
```

然后逐项比较，避免一次同时改变太多参数：

- 后端：PyTorch FP32、ONNX O2 FP32（`onnx/model_O2.onnx`）、ONNX O2 INT8。
- 线程：`--threads 1` 与 `--threads 2`；各次运行是独立进程，避免不可重复设置 inter-op。
- 并发：`--clients 1/2/4`，默认 `--inference-slots 1`；比较包含排队的 wall time 与纯 predict time。线程多/并发大不等于单请求更快。
- 缓存：默认 `--cache cold` 每一波全部请求完成后再清空，不将缓存命中冒充量化收益；`--cache warm` 单独测。cold 模式保持模型已加载，加载耗时单独报告。
- batch：需要降低峰值内存时再比较 `--batch-size 4/8/16`；不改变 max_length 和候选数量。

报告包含 P50/P95、样本量、峰值进程 RSS、实际 token 长度与截断比例、各阶段指标、top-1 一致率、top-3 集合重叠和分数偏差。小样本 P95 只作描述；与 FP32 排序一致不等于答案正确。峰值 RSS 是独立 benchmark 进程，不含完整后端同时驻留的 embedding/索引/会话等内存。

## 4. 2c2g 上线与回退

先只上线线程/并发/trace，保留原 FP32 模型，记录真实负载；再单独切 ONNX。推荐起点：

```dotenv
INFERENCE_CPU_THREADS=2
INFERENCE_INTEROP_THREADS=1
RERANKER_DEVICE=cpu
RERANKER_MAX_CONCURRENT=1
RERANKER_MAX_QUEUE=8
RERANKER_QUEUE_TIMEOUT_SECONDS=30
RERANKER_SCORE_CACHE_SIZE=4096
TOKENIZERS_PARALLELISM=false
OMP_NUM_THREADS=2
MKL_NUM_THREADS=2
```

量化通过同机/同 checkpoint 回归后增加：

```dotenv
RERANKER_BACKEND=onnx
RERANKER_MODEL_PATH=/absolute/path/to/reranker-onnx-avx2
RERANKER_ONNX_FILE=onnx/model_qint8_avx2.onnx
RERANKER_AUTO_DOWNLOAD=false
```

只运行 **1 个 Uvicorn worker**。多个 worker 各有模型、缓存、线程池和队列；每进程限并发 1 不是整台机器限并发 1。离线入口原本已固定一个 worker，本轮保留。

裸部署回退：恢复 `RERANKER_BACKEND=torch` 和原来的 FP32 `RERANKER_MODEL_PATH`，重启。Docker 部署的模型目录由 Compose 固定挂载：必须重新生成带 ONNX 的完整模型包及 manifest，不能只在只读挂载目录塞入一个未校验 graph；回退时恢复旧模型包/配置。原权重与发布包应保留。新增依赖后需重建 Linux 镜像，旧镜像不会因 git pull 自动具有 ONNX 功能。

拥塞时回退混合检索是明确的质量降级，而不是免费加速；检查 `fallback_reason`，不能只看返回耗时。上线前同时记录空闲/并发请求、整体进程 RSS、换页、CPU throttling/steal；若实例是突发性能型，再检查 CPU 积分。本轮没有核验 ECS 的具体 CPU 型号或在线负载。

## 5. 后续优先级

1. 在 ECS 上跑同一批真实候选，选择实际更快且证据覆盖不回退的后端；没有收益则保留 PyTorch，不凭模型文件大小决定。
2. 根据 `model_load_ms` 判断是否需要部署后预热；独立 benchmark 的预热不等于在线服务已预热。
3. 根据排队、预测耗时与 RSS 决定 batch/线程，而不是直接加 worker。高并发持续饱和时，再考虑独立推理服务或扩容。
4. OpenVINO、`torch.compile`、请求间动态 batching 以后按硬件与负载单独验证。本轮不引入新模型、候选阈值、截断策略或新的 LLM 调用。

参考实现依据：Sentence Transformers CrossEncoder efficiency 文档；ONNX Runtime threading / quantization 文档；Optimum-ONNX optimization / quantization 配置文档。对应源码入口为 `src/reranker_backend.py`、`src/inference_runtime.py`、`src/reranker.py`。

## 验证记录

2026-11-12 的本地实测结果见 [RERANKER_PERFORMANCE_RESULTS.md](RERANKER_PERFORMANCE_RESULTS.md)。
Linux x86_64 / Python 3.13 的 ONNX、ORT 二进制 wheel 已做安装解析检查（manylinux_2_28）；完整 Linux 镜像仍需构建和验收。旧于 glibc 2.28 的裸机环境不适用这组固定版本，应使用仓库的 Debian bookworm 容器或另行验证依赖。
`model_load_ms` 是实际初始化耗时；`model_wait_ms` 单列等待另一个请求加载模型的耗时，避免冷启动等待被误判为模型前向计算。
