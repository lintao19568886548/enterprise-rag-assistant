# 一期安全闭环与运维说明

## 已落地边界

- SQL 资源统一带 `tenant_id`：用户、知识库、文档、分块、导入任务、会话和操作日志。
- 当前本机历史数据全部归入 `default` 租户；这是兼容迁移，不会改变原有知识内容。
- 知识库、文档、导入任务、聊天历史和本地图片均在服务端校验租户与用户，不信任前端传入的过滤条件。
- Milvus 检索强制绑定已经通过权限校验的 `knowledge_base_id`；新导入向量同时记录 `tenant_id`。
- 本地图片优先使用 `/images/{task_id}/{filename}` 精确路由；旧图片路由保留兼容，但同样需要鉴权并反查所属任务。
- 回答提示将检索片段、历史对话和网页结果视为不可信数据，拒绝其中的越权指令和密钥索取。

当前一期采用“一个用户属于一个租户”的模型。OIDC/SSO、一个用户加入多个租户、细粒度部门成员关系和企业 KMS 属于下一阶段，不应把静态 API Key 方案误认为最终统一身份平台。

## 数据库迁移

当前迁移版本：

```text
8f1a2c3d4e5f_add_default_tenant_acl
```

执行：

```powershell
uv run alembic upgrade head
uv run alembic current
```

迁移前必须停止 8000、8001 和 Milvus，至少备份：

- `data/knowledge_base.db`
- `data/langgraph_checkpoints.sqlite`
- `data/milvus_local.db/`
- `output/`

本次停机一致性备份位于项目的 `backups/pre_phase1_migration_20260831_2050/`。该目录被 Git 忽略，只用于本机故障恢复。

## 回退

优先使用整库备份回退，避免代码与数据库版本错配：

1. 在 PyCharm 停止“全部服务”。
2. 另存当前故障现场，不要直接覆盖唯一副本。
3. 恢复迁移前的 SQLite、Milvus Lite 与 `output/`。
4. 切回与旧数据库匹配的代码版本。
5. 从 PyCharm 启动“全部服务”，检查两个 `/health/ready`。

`alembic downgrade 2c72d95439c1` 只适合受控测试；它会移除租户字段，不作为生产数据的首选恢复方案。

## 生产配置门槛

切换 `APP_ENV=production` 后，应用会拒绝以下配置：

- 未启用鉴权，或管理员/普通/只读任一角色缺少 API Key。
- API Key 少于 32 个字符，或不同角色复用同一个 Key。
- SQLite、内存任务后端、未启用 Redis 队列。
- 非 PostgreSQL LangGraph Checkpointer、缺少检查点数据库或 AES Key。
- 未显式配置模型白名单。
- 未启用私有 MinIO、缺少凭据或启用公共读。
- CORS 通配符或关闭知识库过滤。

生产密钥必须由密码管理器或密钥服务生成并注入，不要写入仓库、截图、聊天记录或日志。

## 必须人工完成的密钥动作

曾经出现在截图或聊天中的 DashScope/OpenAI 兼容 Key 应视为已暴露。请在百炼控制台撤销旧 Key，创建新 Key，并只更新本机/部署环境的 `.env` 或密钥管理系统。应用无法替你在供应商账户中撤销凭据。

## 验收命令

```powershell
uv run ruff check app tests evaluation alembic
uv run mypy app
uv run pytest -q
uv run python -m app.evaluation.run_eval --dataset evaluation/rag_cases.phase1.jsonl --validate-only
```

运行时验收至少包括：

- `8000/health/ready` 与 `8001/health/ready` 返回 `ready`。
- 非流式问答可完成实体确认、向量检索、回答、引用、图片和历史落库。
- 跨租户知识库、会话、文档、任务和图片请求返回 404/403。
- 管理页与问答页在 390px 宽度无横向溢出。

## 下一阶段

- 接入 OIDC/企业 SSO，并用用户—租户成员关系替换单租户用户假设。
- PostgreSQL 行级安全策略与租户审计报表。
- 删除任务 outbox/补偿机制，覆盖向量、对象、本地文件和元数据的最终一致性。
- 完成 70 条业务标注，建立发布阻断阈值与持续 RAG 回归。
- 将本地兼容图片重新导入为任务级精确 URL，逐步下线旧图片路由。
