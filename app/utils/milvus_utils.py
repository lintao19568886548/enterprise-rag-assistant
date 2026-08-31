from typing import Union


def escape_milvus_string(value: Union[str, None]) -> str:
    """
    Milvus 过滤表达式专用字符串安全转义函数
    核心作用：
        避免因原始字符串含特殊字符，导致 Milvus 解析 filter_expr 时报错，保证 CRUD 操作正常执行
    转义规则：
        1. 反斜杠（\）→ 双反斜杠（\\）：Milvus 表达式转义规则
        2. 双引号（"）→ 转义双引号（\"）：避免截断字符串表达式
        3. 换行/回车/制表符 → 空格：防止表达式换行导致解析失败
    参数：
        value: 需要转义的字符串（如商品名称、文件标题）
    返回：
        str: 转义后的安全字符串，可直接用于 Milvus 的 filter_expr

    使用示例:
        >>> escape_milvus_string('商品"名称')
        '商品\\"名称'
        >>> escape_milvus_string('测试\n文档')
        '测试 文档'
        >>> escape_milvus_string(None)
        ''
    """
    if value is None:
        return ""

    # 确保输入为字符串类型，避免非字符串值报错
    s = str(value)

    # 按 Milvus 规则转义特殊字符
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("'", "\\'")

    # 替换换行/回车/制表符为空格，保证表达式单行有效
    s = s.replace("\r", " ").replace("\n", " ").replace("\t", " ")

    return s


def build_chunk_filter(
    item_names: list[str] | None,
    tenant_id: str | None,
    knowledge_base_id: str | None,
) -> str:
    """Build the mandatory tenant/knowledge-base/active chunk filter."""
    clauses = [build_scope_filter(tenant_id, knowledge_base_id)]
    if item_names:
        values = [f'"{escape_milvus_string(value)}"' for value in item_names if str(value).strip()]
        if values:
            clauses.append(f"item_name in [{', '.join(values)}]")
    return " and ".join(clauses)


def build_scope_filter(
    tenant_id: str | None,
    knowledge_base_id: str | None,
    *,
    document_id: str | None = None,
    item_name: str | None = None,
    active_only: bool = True,
) -> str:
    """Create a filter solely from trusted identifiers; raw expressions are never accepted."""
    if not tenant_id or not str(tenant_id).strip():
        raise ValueError("tenant_id is required for every Milvus operation")
    if not knowledge_base_id or not str(knowledge_base_id).strip():
        raise ValueError("knowledge_base_id is required for every Milvus operation")
    clauses = [
        f'tenant_id == "{escape_milvus_string(tenant_id)}"',
        f'knowledge_base_id == "{escape_milvus_string(knowledge_base_id)}"',
    ]
    if active_only:
        clauses.append("is_active == true")
    if document_id is not None:
        if not str(document_id).strip():
            raise ValueError("document_id cannot be blank")
        clauses.append(f'document_id == "{escape_milvus_string(document_id)}"')
    if item_name is not None:
        if not str(item_name).strip():
            raise ValueError("item_name cannot be blank")
        clauses.append(f'item_name == "{escape_milvus_string(item_name)}"')
    return " and ".join(clauses)
