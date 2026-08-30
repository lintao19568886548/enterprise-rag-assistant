# 导入核心依赖（和其他配置类共用，只需导入一次）
from dataclasses import dataclass
import os
from dotenv import load_dotenv

# 提前加载.env配置文件（全局执行一次即可，无需重复写）
load_dotenv()

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
    milvus_url=os.getenv("MILVUS_URL"),
    keepalive_time_ms=int(os.getenv("MILVUS_KEEPALIVE_TIME_MS", "300000")),
    keepalive_timeout_ms=int(os.getenv("MILVUS_KEEPALIVE_TIMEOUT_MS", "20000")),
    keepalive_permit_without_calls=os.getenv(
        "MILVUS_KEEPALIVE_PERMIT_WITHOUT_CALLS", "False"
    ).strip().lower() in {"1", "true", "yes", "on"},
    chunks_collection=os.getenv("CHUNKS_COLLECTION"),
    entity_name_collection=os.getenv("ENTITY_NAME_COLLECTION"),
    item_name_collection=os.getenv("ITEM_NAME_COLLECTION")
)
