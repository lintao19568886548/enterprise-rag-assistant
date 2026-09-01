from dataclasses import dataclass

from app.core.settings import settings


# 定义MinIO对象存储服务配置（与LLMConfig风格一致，字段对应.env配置项）
@dataclass
class MinIOConfig:
    enabled: bool   # 是否启用远程MinIO；关闭时使用本地output图片服务
    endpoint: str    # MinIO服务地址（含http/https和端口）
    public_endpoint: str
    access_key: str | None  # MinIO访问密钥（对应MINIO_ACCESS_KEY）
    secret_key: str | None  # MinIO秘钥（对应MINIO_SECRET_KEY）
    bucket_name: str # MinIO默认存储桶名（知识库文件专用）
    minio_img_dir: str #Minio存储图片的文件夹
    minio_secure: bool # 是否使用ssl加密 http 还是 https
    public_secure: bool
    public_read: bool
    presigned_expiry_seconds: int


# 实例化MinIO配置对象，自动从.env读取配置并绑定
minio_config = MinIOConfig(
    enabled=settings.minio_enabled,
    endpoint=settings.minio_endpoint,
    public_endpoint=settings.minio_public_endpoint,
    access_key=settings.reveal(settings.minio_access_key),
    secret_key=settings.reveal(settings.minio_secret_key),
    bucket_name=settings.minio_bucket_name,
    minio_img_dir=settings.minio_img_dir,
    minio_secure=settings.minio_secure,
    public_secure=settings.minio_public_secure,
    public_read=settings.minio_public_read,
    presigned_expiry_seconds=settings.minio_presigned_expiry_seconds,
)
