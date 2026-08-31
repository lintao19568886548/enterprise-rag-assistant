from dataclasses import dataclass

from app.core.settings import settings


# 定义Embedding配置（适配BGE-M3的所有配置，类名embedding_config）
@dataclass
class EmbeddingConfig:
    bge_m3_path: str | None  # 本地模型路径
    bge_m3: str       # 模型仓库标识
    bge_device: str   # 运行设备(cuda:0/cpu)
    bge_fp16: bool    # 是否开启半精度（1=True/0=False）

# 实例化配置对象，和原代码lm_config风格保持一致
embedding_config = EmbeddingConfig(
    bge_m3_path=settings.bge_m3_path,
    bge_m3=settings.embedding_model,
    bge_device=settings.bge_device,
    bge_fp16=settings.bge_fp16,
)
