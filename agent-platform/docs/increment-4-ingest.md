# 增量 4 · ingest 摄入（4.1–4.6）设计与接口记录

> 编制：随研发 ｜ SUIG-18 ｜ 依据：`increment-1-breakdown.md` + 架构裁决 #3/#4/#5

## 1. 达成口径（验收对照）

| # | 子任务 | 落地文件 | 验收证据 |
|---|---|---|---|
| 4.1 | 文档解析器 | `ingest/parser/__init__.py` | `tests/test_ingest_parser.py`：四格式样例解析成功；超限 / 非白名单拒绝 |
| 4.2 | 切分器 + **tokenizer 选型定稿** | `ingest/chunker.py`、`ingest/tokenizer.py` | `test_ingest_chunker.py`：切片数与 token 分布符合预期；选型取舍记录见 §3 |
| 4.3 | Embedding 接入 | `ingest/embedding.py` | `test_ingest_embedding.py`：维度 = `MILVUS_DIM`；批处理；失败可重试 |
| 4.4 | 双写 + 状态机补偿 | `ingest/storage.py`、`ingest/worker.py`、`core/storage/{milvus,es}.py` | `test_ingest_integration.py`：**ES/Milvus 分别注错 → FAILED(3) → 重跑 → DONE**，残留不增 |
| 4.5 | worker 重试与幂等 | `ingest/worker.py` | `test_ingest_integration.py`：重复投递切片不翻倍；`(kb_id,file_hash)` 唯一约束 |
| 4.6 | 端到端摄入 | worker 全链路 | `test_ingest_integration.py`：摄入后 Milvus 向量 + ES 关键词均可命中 |

## 2. 架构：worker 消费端（本增量补的是 2.4 之后的消费端）

```
gateway POST /v1/doc (2.4, 已有)                       ingest worker (本增量)
  校验→落盘→write document(status=0)→入队        ┌─ load_document ─ claim(PROCESSING)
                                                │─ resolve path → validate_document(≤200MB)
                                                │─ extract_text (pdf/docx/md/txt)
                                                │─ split_text (~250 token / overlap 100)
                                                │─ embed_texts (batch=MILVUS_DIM)
                                                │─ write_chunks: Milvus.insert + ES.bulk_index（串行双写）
                                                │─ mark_done(chunk_count)
                                                └─ 任意异常 → mark_failed → arq 退避重跑
```

- **状态机**：`document.status` 是唯一真相源（裁决 #3，不做分布式事务）
  `PENDING(0) --claim--> PROCESSING(1) --ok--> DONE(2)` / `--异常--> FAILED(3)`。
- **重跑可重入**：`FAILED → PROCESSING` 认领成功后先 `cleanup_partial`（ES `delete_by_query` + Milvus `delete_by_doc_id`）清残留；`chunk_id` 作为 ES `_id` / Milvus 主键 → 重复写天然幂等覆盖。
- **同步阻塞修复**（增量 3 登记 TODO 收口）：`worker.cleanup_partial` 不再直接调同步 `milvus.client.delete`，改走存储层 `MilvusStore.delete_by_doc_id()`（`asyncio.to_thread`）。

## 3. **tokenizer 选型决策（4.2 定稿，此前为增量 1/2 开放项）**

**结论：运行时默认用确定性离线估算器**（`ingest/tokenizer.py`），不引入 tiktoken / BGE 词汇表等需联网下载的第三方契约。

理由：
1. 运行/环境无真实模型下载授权（本 increment 既定约束：嵌入 echo 桩、LLM echo provider，tokenizer 同理强依赖离线不可用，违背「未验证契约不引入」）。
2. 确定性 + 零下载：同输入恒同切分，可离线复现、可单测精确断言。
3. **可插拔**：`Tokenizer` 协议只暴露 `encode / count`，真实接入（tiktoken `cl100k_base` / BGE 自带）在 4/5 联动时替换 `_TOKENIZER_IMPL` 即可。

估算规则：CJK（中日韩）单字符 ≈ 1 token；连续 ASCII/数字按 4 字符 ≈ 1 token；空白不计入计数但保留在 token 序列内（**无损重建**：`"".join(encode(s))==s`）。

**`message.content` 摘要长度约定（一并定稿）**：正文摘要收敛到 ≤ **_摘要_MAX ≈ 128 token**（约 chunk(250) 的一半），避免大文本进 MySQL `message.content`——MySQL 只承载 `es_chunk_ref` 引用，正文在 ES（设计文档 §5.3）。

## 4. 接口契约

### 4.1 解析器（`ingest.parser`）
- `validate_document(path) -> None`：白名单（`.pdf/.docx/.md/.txt`）+ ≤`INGEST_MAX_DOCUMENT_MB`(200)。超限/非白名单 → `ValidationError`。
- `extract_text(path) -> str`：按扩展名分发；损坏/无文本 → `ParserError`（worker 置 FAILED）。

### 4.2 切分器（`ingest.chunker`）
- `split_text(text, *, chunk_tokens=250, overlap=100, title=None) -> list[Chunk]`
- `Chunk(index, text, token_count)`；每个切片的 `index` 即 ES `chunk_index`。

### 4.3 Embedding（`ingest.embedding`）
- `embed_texts(texts, *, batch_size=32) -> list[list[float]]`：输出维度 = `MILVUS_DIM`（768）；任一批失败抛 `UpstreamError`（可重试）。
- `embed_query(text) -> list[float]`：检索路径，与建库同一模型。
- 复用 `langgraph_service.retrieval._stub_embed`（echo 桩，位级与检索一致 → e2e 闭环前提）。

### 4.4 双写与清理（`ingest.storage`）
- `write_chunks(*, container, kb_id, doc_id, chunks, vectors, title) -> int`：串行 Milvus.insert → ES.bulk_index，任一侧失败抛 `UpstreamError`。
- `cleanup(container, *, kb_id, doc_id)`：清两侧残留（可重入）。
- `build_chunk_id(doc_id, index) -> str`：`"{doc_id}:{index:06d}"`，ES `_id` / Milvus 主键同一份。

## 5. 所需交付物清单
- 代码：以上模块 + 单测 + 集成测试（已含）
- 数据模型：`document` 字段不变；新增切片在 Milvus/ES 的字段见 `db/schemas_milvus` / `db/mappings_es`
- 运行说明：见 repo `README.md` + §6
- 编码自测清单：见 `tests/test_ingest_*.py`（7 组，含 6 项集成）

## 6. 运行说明（ingest worker）
```bash
cd agent-platform
.venv/Scripts/python.exe -m pip install -e ".[dev,parser]"   # 一次
.venv/Scripts/python.exe -m pytest -q                          # 全部测试（含真实六服务集成）
arq ingest.worker.WorkerSettings                               # 启动摄入 worker
```
- 环境依赖：mysql/redis/milvus/es 已在跑（docker compose ap-*）；`.env` 按 `.env.example` 指向本机。
- 权限/资源：无外部模型调用（embedding echo 桩），无付费下载。

## 7. 已知取舍
- Embedding/LLM 均为 echo 桩：真实模型在 4/5 联动按 provider 契约替换（届时核实模型资源与授权）。
- ES 写入 `time_to_refresh`：`bulk_index` 用 `refresh=True` 保证写入后可检索（4.6 e2e 依赖）。