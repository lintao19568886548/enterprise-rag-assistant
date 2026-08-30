# 导入Python内置模块
import json
# 导入MinIO官方Python SDK核心类
from minio import Minio
# 项目内部配置与日志
from app.conf.minio_config import minio_config
from app.core.logger import logger

# 全局MinIO客户端对象。仅在首次真正使用对象存储时建立连接，
# 避免远端服务不可用时阻塞 FastAPI 模块导入和健康检查。
minio_client = None


def _create_minio_client():
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

    bucket_policy = {
        "Version": "2012-10-17",
        "Statement": [{
            "Effect": "Allow",
            "Principal": {"AWS": ["*"]},
            "Action": ["s3:GetObject"],
            "Resource": [f"arn:aws:s3:::{bucket_name}/*"]
        }]
    }
    client.set_bucket_policy(bucket_name, json.dumps(bucket_policy))
    logger.info(f"MinIO存储桶[{bucket_name}]连接成功并已配置只读访问策略")
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

if __name__ == "__main__":
    # 测试代码，请勿删除
    get_minio_client()
    pass
