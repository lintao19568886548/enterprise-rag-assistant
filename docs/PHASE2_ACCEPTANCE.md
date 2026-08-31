# 二期企业生产化验收与回退说明

## 结论

二期代码实现、开发机运行链路、安全边界、恢复演练、管理工作台和 PR 质量门禁已完成并通过真实验收。当前分支可以进入代码评审，但不能标记为“生产发布完成”：真实 PostgreSQL RLS、Redis/Celery、MinIO、OIDC 和 Docker Compose 联调尚缺少本机基础设施或真实参数；100 条评测集中仍有 70 条等待业务专家标注，因此 release gate 必须保持阻塞。

机器可读结果位于 [`docs/reports/phase2_final_acceptance.json`](reports/phase2_final_acceptance.json)。

## 本机真实运行验收

验收时间：`2026-09-01 00:43 +08:00`。

PyCharm 运行配置“全部服务 (19530 + 8000 + 8001)”使用项目 `.venv` 解释器启动成功：

- Milvus Lite：`127.0.0.1:19530`；
- 文件导入与管理 API：`http://127.0.0.1:8000`；
- 企业问答 API：`http://127.0.0.1:8001`；
- 两个 `/health/ready` 都返回 HTTP 200 和 `ready`。

浏览器真实验收覆盖 `import.html`、`admin.html` 和 `chat.html`：

- 默认桌面视口没有页面级横向溢出或控制台 warning/error；
- 390×844 移动端没有页面级横向溢出或控制台 warning/error；
- 管理台的模块标签在 390px 下使用组件内部 `overflow-x: auto`，不会扩张页面宽度；
- 管理台成功读取租户范围内的知识库、文档、成员、任务、补偿、评测和审计数据。

真实问答问题为：

> H3C LA2608 室内无线网关如何验证与无线控制器已经连通？请给出命令、判断标准，并引用资料。

结果：

- 7 个 LangGraph 阶段全部完成；
- 回答给出 `display wlan ap all` 命令和 `State=R/M` 判断标准；
- 证据置信度 89%；
- 5 条引用和 4 张本次回答图片；
- 模型 `qwen-flash`，数据库记录端到端耗时 `1904 ms`；
- 会话 `sess-hzxqdoacmvomtf5dxsv`；
- 租户 `00000000-0000-0000-0000-000000000100`；
- 用户 `00000000-0000-0000-0000-000000000001`；
- 知识库 `00000000-0000-0000-0000-000000000010`；
- 页面控制台无 warning/error，问答历史已落库。

## 数据库与向量数据

SQLite 开发库已迁移至 Alembic `d4e5f6a7b8c9 (head)`，`PRAGMA integrity_check=ok`，外键错误为 0。本轮验收结束时有 1 个租户、1 个用户、1 个成员、2 个知识库、3 个文档、3 个文档版本、4 个导入任务、21 个会话和 72 条消息。软删除或历史版本不会出现在默认管理列表中，但仍保留在数据库中。

Milvus 数据与别名：

- `kb_chunks_active -> kb_chunks_phase2_v2`，49 条；
- `kb_item_names_active -> kb_item_names_phase2_v2`，3 条；
- 源集合 `kb_chunks`、`kb_item_names` 和历史备份集合仍保留；
- 100 轮真实跨租户向量检索返回 0 条越权数据；P50 `4.617 ms`，P95 `7.573 ms`，P99 `10.321 ms`；
- 隔离验收只创建一次性测试集合，结束后已自动删除。

## 自动化质量门禁

- Pytest：`91 passed, 2 skipped`；
- 两个 skip 是未配置真实 PostgreSQL owner/runtime URL 的 RLS 集成测试；
- Ruff：通过；
- Mypy：98 个源码文件通过；
- Python 编译检查：通过；
- 30 条已批准评测的离线 scorer-contract PR gate：30/30 通过；
- 评测数据结构校验：100 条、30 条 approved、70 条 `needs_human_label`、0 个结构错误；
- 本地并发性能基线见 [`PHASE2_PERFORMANCE.md`](PHASE2_PERFORMANCE.md)，恢复演练见 [`DISASTER_RECOVERY.md`](DISASTER_RECOVERY.md)。

离线 scorer-contract 只验证评分器和 PR 门禁，不代表在线模型质量。release gate 还要求已批准的 prompt-injection 与 permission-isolation 业务评测；当前相关条目仍在 70 条待标注数据中，所以没有运行会产生误导的“全量在线发布通过”声明。

## 已完成的安全与运维能力

- OIDC 身份模型、成员与角色、知识库授权和服务账户；
- PostgreSQL fail-closed RLS 迁移及真实集成测试入口；
- 文档版本、软删除、Milvus 主动别名和删除补偿 outbox；
- 租户、知识库、文档、任务、SSE、会话、图片和反馈的服务端权限隔离；
- 模型输出泄漏防护、提示注入防护和不可变审计；
- Prometheus 指标、告警规则、模型 token/成本、节点耗时和任务队列指标；
- 企业管理工作台、导入页和问答页的桌面与移动端界面；
- SQLite 隔离恢复演练、备份与恢复脚本、生产形态 staging Compose。

安全验收细节见 [`PHASE2_SECURITY_ACCEPTANCE.md`](PHASE2_SECURITY_ACCEPTANCE.md)，生产部署约束见 [`STAGING.md`](STAGING.md)。

## 尚未解除的发布阻塞

以下事项不能在当前电脑上伪造通过：

1. Docker CLI/Engine 与真实 PostgreSQL、Redis、MinIO 未安装或未运行，故完整 Compose、真实 RLS、多 worker、对象存储和故障注入仍需 staging 环境执行。
2. 真实企业 OIDC issuer、client ID、client secret、redirect URI 和组织映射未提供，当前只能验收实现与模拟测试。
3. 70 条评测需要业务专家填写问题、标准答案、证据页码、租户/知识库范围和审批信息；在此之前 release gate 应失败。
4. 之前截图中出现的 DashScope Key 应按已泄露处理：必须在百炼控制台撤销旧 Key、创建新 Key，并只更新本机 `.env`。
5. 仓库 LICENSE 类型尚未由所有者确认，不代替所有者选择许可证。

## 回退步骤

本轮开始前的一致性备份位于：

`D:\XiangMu\企业只能问答系统\3.代码\knowledge_base_0410\backups\pre_phase2_20260831_215908`

回退顺序：

1. 在 PyCharm 停止“全部服务”，并阻断新的导入与问答写入。
2. 记录当前 Alembic revision、数据库文件和 Milvus alias 指向。
3. 数据库优先使用 Alembic 逐级 downgrade；若必须整库恢复，使用上述停机一致性备份并先在隔离目录验证。
4. Milvus 回退只切换 `kb_chunks_active` 与 `kb_item_names_active` 到保留的旧集合；不要删除 v2 或 legacy 集合。
5. 恢复后检查两个 `/health/ready`、数据库外键、租户计数、别名计数和一次真实问答。
6. 若失败，保持服务停止并保留失败现场，不覆盖现有备份。

详细备份/恢复命令和 RPO/RTO 说明以 [`DISASTER_RECOVERY.md`](DISASTER_RECOVERY.md) 为准。

## 发布决策

- 允许：推送 `phase2/enterprise-production` 并创建面向 `main` 的 Pull Request 进行评审。
- 不允许：自动合并 PR、把离线 fixture 当成在线质量、把 SQLite 当成 PostgreSQL RLS 验收、删除旧 Milvus 集合、伪造 70 条业务审批或声称生产 release gate 已通过。
