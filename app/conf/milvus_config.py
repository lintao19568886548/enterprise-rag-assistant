from dataclasses import dataclass

from app.core.settings import settings

# ===================== 其他配置类（LLM/Embedding）可放在上方，保持原有代码不变 =====================
# ... 你的LLMConfig、EmbeddingConfig代码 ...

# 定义Milvus向量数据库配置类
@dataclass
class MilvusConfig:
    milvus_url: str          # Milvus服务端连接地址
    keepalive_time_ms: int   # gRPC心跳间隔，避免Milvus Lite因心跳过密发送GOAWAY
    keepalive_timeout_ms: int
    keepalive_permit_without_calls: bool
    chunks_collection: str   # 存储切片的集合名称
    entity_name_collection: str  # 预留-实体名称集合
    item_name_collection: str    # 存储文档对应实体类的集合名称

# 实例化Milvus配置对象（和其他配置对象命名风格统一）
milvus_config = MilvusConfig(
    milvus_url=settings.milvus_uri,
    keepalive_time_ms=settings.milvus_keepalive_time_ms,
    keepalive_timeout_ms=settings.milvus_keepalive_timeout_ms,
    keepalive_permit_without_calls=settings.milvus_keepalive_permit_without_calls,
    chunks_collection=settings.milvus_collection,
    entity_name_collection=settings.entity_name_collection,
    item_name_collection=settings.item_name_collection,
)
