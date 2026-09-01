# 企业知识库 RAG 系统

这是一个基于 FastAPI、LangGraph、BGE-M3 和 Milvus 的企业文档问答系统。系统支持 PDF/Markdown 导入、MinerU 解析、图片理解、文档切分、稠密与稀疏向量检索、HyDE、RRF、重排、流式回答和聊天历史。

当前默认开发模式保留原有的本机运行方式：Milvus Lite 监听 `19530`，文件导入服务监听 `8000`，问答服务监听 `8001`。生产模式会强制启用鉴权、PostgreSQL、Redis 队列、私有 MinIO 和加密检查点，并禁止内存任务后端。

## 架构

```text
Browser
  ├─ http://127.0.0.1:8000/import.html
  ├─ http://127.0.0.1:8000/admin.html
  └─ http://127.0.0.1:8001/chat.html
          │
     FastAPI services
       ├─ request ID / CORS / rate limit / RBAC
       ├─ upload validation
       ├─ health and metrics
       └─ LangGraph workflows
          │
   ┌──────┼──────────┬──────────┐
 Milvus  Redis     Database    MinIO
 vectors tasks     metadata    files/images
```

开发模式默认使用 SQLite 元数据库、SQLite LangGraph Checkpointer、内存任务状态、Milvus Lite 和本地 `output/` 文件；Redis 与 MinIO 可以关闭。生产模式使用 Redis、Celery、PostgreSQL、MinIO 和 Milvus Standalone，并强制知识库隔离与鉴权。

## 环境要求

- Windows 10/11
- Python 3.11
- [uv](https://docs.astral.sh/uv/)
- PyCharm Community/Professional 2024 或更新版本（可选）
- 本地开发：Milvus Lite
- 完整基础设施：Docker Desktop（WSL2 后端）与 Docker Compose
- 可用的 DashScope/OpenAI 兼容 API Key
- 可用的 MinerU API Token

由于 BGE-M3 和 Torch 占用空间较大，建议预留至少 10 GB 磁盘空间和 8 GB 内存。CPU 模式可运行，但首次模型加载和批量导入会比较慢。

## 安装

```powershell
Set-Location -LiteralPath 'D:\XiangMu\企业只能问答系统\3.代码\knowledge_base_0410'
uv sync --all-groups
Copy-Item -LiteralPath '.env.example' -Destination '.env'
```

编辑 `.env`，至少配置：

```ini
OPENAI_API_KEY=replace-with-a-new-key
OPENAI_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
MINERU_API_TOKEN=replace-with-mineru-token
MILVUS_URI=http://127.0.0.1:19530
```

真实 `.env` 已被 Git 忽略。不要把密钥写进 `.env.example`、源码、测试或日志。

## 配置模式

### 开发模式

```ini
APP_ENV=development
AUTH_ENABLED=False
REDIS_ENABLED=False
TASK_BACKEND=memory
DATABASE_ENABLED=True
DATABASE_URL=sqlite:///./data/knowledge_base.db
LANGGRAPH_CHECKPOINTER=sqlite
LANGGRAPH_CHECKPOINT_PATH=./data/langgraph_checkpoints.sqlite
MINIO_ENABLED=False
WEB_SEARCH_ENABLED=False
```

### 生产模式

生产模式会在启动时拒绝以下不安全配置：

- `AUTH_ENABLED=False`
- `TASK_BACKEND=memory`
- `CORS_ALLOWED_ORIGINS=*`

生产环境应为管理员、普通用户和只读用户生成不同的长随机 API Key：

```ini
APP_ENV=production
AUTH_ENABLED=True
ADMIN_API_KEYS=replace-with-random-admin-key
USER_API_KEYS=replace-with-random-user-key
READONLY_API_KEYS=replace-with-random-readonly-key
TASK_BACKEND=redis
REDIS_ENABLED=True
```

受保护接口通过 `X-API-Key` 请求头认证。开发模式关闭鉴权时，后端使用仅限本机的开发管理员身份。

一期租户隔离、密钥轮换、迁移与回退要求详见 [`docs/PHASE1_SECURITY.md`](docs/PHASE1_SECURITY.md)。

所有支持的配置及安全默认值参见 [`.env.example`](.env.example)。旧变量 `MILVUS_URL`、`LLM_DEFAULT_MODEL`、`TEXT_RERANK_MODEL` 等暂时继续兼容。

## 命令行启动

### 1. Milvus Lite

```powershell
.venv\Scripts\python.exe -m milvus_lite server `
  --data-dir 'D:/milvus_data/knowledge_base_0410' `
  --host 127.0.0.1 `
  --port 19530
```

### 2. 数据库迁移

```powershell
.venv\Scripts\alembic.exe upgrade head
```

### 3. 文件导入服务

```powershell
.venv\Scripts\python.exe app\import_process\api\file_import_service.py
```

### 4. 问答服务

```powershell
.venv\Scripts\python.exe app\query_process\api\query_service.py
```

服务页面：

- 文件导入：http://127.0.0.1:8000/import.html
- 企业管理工作台：http://127.0.0.1:8000/admin.html
- 问答：http://127.0.0.1:8001/chat.html
- 导入 Swagger：http://127.0.0.1:8000/docs
- 问答 Swagger：http://127.0.0.1:8001/docs

二期本机真实验收、质量门禁、已知外部阻塞和回退入口见
[`docs/PHASE2_ACCEPTANCE.md`](docs/PHASE2_ACCEPTANCE.md)。

## PyCharm 启动

项目已包含可共享 Run Configuration：

- `本地向量库 (19530)`
- `导入服务 (8000)`
- `问答服务 (8001)`
- `全部服务 (19530 + 8000 + 8001)`
- `数据库迁移 (Alembic)`
- `任务 Worker (Windows/Redis)`
- `工程测试 (pytest)`

在 PyCharm 中选择项目 `.venv\Scripts\python.exe` 作为解释器，先运行“数据库迁移”，再运行“全部服务”即可。开发模式的导入任务在 API 进程执行；启用 Redis 队列后再单独启动 Windows Worker。启动前请确认 `.env` 已配置，且端口没有被旧进程占用。

## Docker Compose

`compose.yaml` 已定义 etcd、MinIO、Milvus Standalone、Redis、PostgreSQL、数据库迁移、导入 API、问答 API 和 Celery Worker。当前电脑本轮验收时 Docker 引擎未运行，因此 Compose 已做静态配置检查，运行链路使用上面的 PyCharm/本机模式完成。

```powershell
docker compose up -d --build
docker compose ps
docker compose logs -f query-api import-api worker
docker compose down
```

在 Compose 文件进入真实验收前，不要使用 `down -v`，因为 `-v` 会删除数据库和对象存储卷。

## 健康检查和指标

两个服务都提供：

- `/health/live`：进程存活。
- `/health/ready`：必要配置、Milvus 及已启用依赖是否可用。
- `/metrics`：Prometheus 格式的请求数和延迟指标。

示例：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/ready
Invoke-RestMethod http://127.0.0.1:8001/health/ready
```

## 安全上传

上传服务默认：

- 只允许 `.pdf`、`.md`、`.markdown`。
- 校验扩展名、MIME、PDF 文件签名和 Markdown UTF-8 内容。
- 拒绝路径穿越、Windows 保留文件名和过长文件名。
- 单文件最大 50 MB。
- 单次最多 10 个文件。
- 使用 UUID 作为服务端文件名。
- 使用 SHA-256 标识内容。
- 校验失败时清理临时文件。

这些限制可通过 `UPLOAD_*` 环境变量调整。

## 测试和代码检查

```powershell
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\ruff.exe check app tests
.venv\Scripts\mypy.exe app
```

版本化 RAG 种子评测集位于 `evaluation/rag_cases.v1.jsonl`，包含 30 条可回答与拒答样例。`evaluation/rag_cases.phase1.jsonl` 是一期 100 条评测骨架，其中 30 条已批准、70 条待业务专家标注，默认运行时不会把待标注项当成真实答案：

```powershell
.venv\Scripts\python.exe -m app.evaluation.run_eval
.venv\Scripts\python.exe -m app.evaluation.run_eval --dataset evaluation/rag_cases.phase1.jsonl --validate-only
# 快速抽样
.venv\Scripts\python.exe -m app.evaluation.run_eval --limit 5 --output evaluation/reports/smoke.json
```

评测报告包含关键词召回、来源召回、拒答准确率、引用有效性、引用覆盖率、通过率和 P50/P95 端到端延迟。答案需要澄清产品时，数据集中的 `confirmation` 会在同一会话自动完成第二轮确认。

测试目录：

```text
tests/
  unit/         不依赖外部服务
  api/          FastAPI 接口契约
  integration/  Redis/PostgreSQL/Milvus/MinIO
  workflow/     完整 LangGraph 流程
  evaluation/   RAG 质量评测
  fixtures/     受控测试数据
```

旧的 `test/` 目录是历史实验脚本，不会被 pytest 自动收集。正式自动测试统一放在 `tests/`。

## 常见问题

### `too_many_pings`

保持以下 gRPC 心跳设置，不要将心跳恢复为 10 秒：

```ini
MILVUS_KEEPALIVE_TIME_MS=300000
MILVUS_KEEPALIVE_TIMEOUT_MS=20000
MILVUS_KEEPALIVE_PERMIT_WITHOUT_CALLS=False
```

### `uv sync` 无法替换 FAISS/PYD

Windows 正在运行的 Python 进程会锁定 `.pyd`。先在 PyCharm 停止 8000、8001 和 Milvus 运行配置，再执行 `uv sync --all-groups`。

### readiness 返回 503

查看响应中的 `components`。`unavailable` 表示已启用且必须连接的依赖不可用；`disabled` 表示开发模式主动关闭，不属于故障。不要通过删除健康检查来掩盖连接失败。

### 旧 Milvus collection 缺少元数据字段

服务会检查 `knowledge_base_id`、`document_id`、`version`、`is_active`、`file_name` 等字段。Milvus Lite 不支持在线 `add_collection_field` 时，系统会把旧 collection 重命名为带时间戳的 `*_legacy_backup_*`，复制旧向量并建立启用动态字段的新 collection；备份不会自动删除。确认新 collection 的数量、检索和引用都正确后，再由管理员决定何时归档旧备份。

### MongoDB 未连接

聊天历史接口保留 MongoDB 兼容入口，但当前实现会回退到统一 SQL 元数据库，因此 MongoDB 不可用不会丢失本地会话历史。生产环境应直接使用 PostgreSQL。

## 数据备份和恢复

### 当前本机开发数据

停止相关服务后备份：

- Milvus Lite：`D:\milvus_data\knowledge_base_0410`
- SQLite 元数据：项目 `data\knowledge_base.db`
- LangGraph Checkpoint：项目 `data\langgraph_checkpoints.sqlite`
- 导入原文和解析产物：项目 `output\`
- 本地日志：项目 `logs\`
- `.env`：单独安全保存，不要放入普通代码备份

恢复时先保持服务停止，将数据恢复到原路径，再启动 Milvus、导入服务和查询服务。恢复前应保留目标目录的额外副本，避免覆盖唯一数据。

### Compose 数据

Compose 部署完成后，PostgreSQL 使用 `pg_dump`/`pg_restore`，MinIO 使用 `mc mirror`，Milvus 按官方备份工具执行。不要只复制运行中的数据库文件。

## 已实现能力

- Git 可回滚基线和安全 `.gitignore`。
- Pydantic Settings 强类型配置及旧变量兼容。
- 开发/测试/生产配置约束。
- 安全流式上传和稳定错误码。
- CORS 白名单、请求 ID、限流、API Key 和 admin/user/readonly 角色权限。
- liveness、readiness 和 Prometheus 指标。
- LangGraph 单进程复用和启动预编译。
- Redis 任务状态/SSE Streams、Celery Worker、SQLite/PostgreSQL 元数据和 Checkpointer。
- 知识库、文档、版本、任务、重建、重试、取消和幂等导入接口。
- 稠密/稀疏双路检索、HyDE、RRF、重排、知识库过滤、拒答、结构化引用、页码、图片、置信度和模型延迟。
- DashScope/OpenAI 兼容模型网关的超时、重试、回退与熔断。
- 100 条版本化 RAG 评测集（30 条已批准、70 条待业务专家标注）、pytest、Ruff、mypy、pre-commit 和 CI。

## 三期准生产文档

- `docs/PHASE3_ACCEPTANCE.md`：本机真实验收、未验证项和发布阻塞项。
- `docs/PRODUCTION_READINESS.md`：准生产判定、staging 启停和发布流程。
- `docs/RELEASE_CHECKLIST.md`：发布前 fail-closed 检查清单。
- `docs/ROLLBACK_CHECKLIST.md`：数据保全优先的回滚清单。
- `docs/OIDC_OPERATIONS.md` 和 `docs/POSTGRES_RLS_ACCEPTANCE.md`：身份与数据库隔离运维。
- `docs/RAG_EVALUATION.md` 和 `docs/PERFORMANCE_BASELINE.md`：评测与性能证据边界。
- `docs/OBSERVABILITY.md`、`docs/INCIDENT_RUNBOOK.md`、`docs/DISASTER_RECOVERY.md`：监控、值班和恢复。

## 生产上线前检查

- 轮换任何曾在截图、聊天或日志中暴露过的 API Key，并只在安全的密钥管理系统中配置新值。
- 把 `APP_ENV` 设为 `production`，启用鉴权、Redis 队列、PostgreSQL、MinIO 和知识库过滤；为不同角色使用不同的随机密钥。
- 将 CORS 精确限制为实际前端域名，所有外部流量通过 HTTPS 反向代理进入，8000/8001/19530/5432/6379/9000 不直接暴露公网。
- 执行数据库迁移、30 条评测集、并发压测和备份恢复演练，再逐步放量。
