# 导入Python内置模块
from datetime import timedelta
from typing import cast
# 导入MinIO官方Python SDK核心类
from minio import Minio
# 项目内部配置与日志
from app.conf.minio_config import minio_config
from app.core.logger import logger

# 全局MinIO客户端对象。仅在首次真正使用对象存储时建立连接，
# 避免远端服务不可用时阻塞 FastAPI 模块导入和健康检查。
minio_client = None


def _create_minio_client():
    if not minio_config.access_key or not minio_config.secret_key:
        raise ValueError("MinIO credentials are required when MinIO is enabled")
    client = Minio(
        endpoint=minio_config.endpoint,
        access_key=minio_config.access_key,
        secret_key=minio_config.secret_key,
        secure=minio_config.minio_secure,
    )
    bucket_name = minio_config.bucket_name

    if not client.bucket_exists(bucket_name):
        logger.info(f"MinIO存储桶[{bucket_name}]不存在，开始创建")
        client.make_bucket(bucket_name)
        logger.info(f"MinIO存储桶[{bucket_name}]创建成功")

    if not minio_config.public_read:
        try:
            client.delete_bucket_policy(bucket_name)
        except Exception:
            pass
    logger.info("MinIO存储桶[{}]连接成功，公开读取={}", bucket_name, minio_config.public_read)
    return client


def get_minio_client():
    """
    获取全局初始化的MinIO客户端实例
    :return: 已初始化的Minio对象 / None（初始化失败时）
    """
    global minio_client
    if not minio_config.enabled:
        return None
    if minio_client is None:
        try:
            minio_client = _create_minio_client()
        except Exception as e:
            logger.error(f"MinIO客户端初始化失败：{str(e)}")
            return None
    return minio_client


def minio_object_uri(bucket_name: str, object_name: str) -> str:
    return f"minio://{bucket_name}/{object_name}"


def presign_minio_uri(uri: str) -> str:
    """Turn an internal minio:// reference into a short-lived browser URL."""
    if not uri.startswith("minio://"):
        return uri
    path = uri.removeprefix("minio://")
    bucket_name, separator, object_name = path.partition("/")
    if not separator or not bucket_name or not object_name:
        return ""
    if not minio_config.access_key or not minio_config.secret_key:
        raise ValueError("MinIO credentials are required for URL signing")
    public_client = Minio(
        endpoint=minio_config.public_endpoint,
        access_key=minio_config.access_key,
        secret_key=minio_config.secret_key,
        secure=minio_config.public_secure,
    )
    return public_client.presigned_get_object(
        bucket_name,
        object_name,
        expires=timedelta(seconds=minio_config.presigned_expiry_seconds),
    )

if __name__ == "__main__":
    # 测试代码，请勿删除
    get_minio_client()
    pass
