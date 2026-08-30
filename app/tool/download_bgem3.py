from modelscope.hub.snapshot_download import snapshot_download

# 下载模型到当前目录下的 models/bge-m3 文件夹
model_dir = snapshot_download(
    'BAAI/bge-m3',
    cache_dir='D:/ai_models/modelscope_cache/models',
    # 运行时使用 PyTorch 权重；跳过同样约 2.3 GB、项目不会读取的 ONNX 副本。
    ignore_patterns=['*.onnx', '*.onnx_data'],
)
print(f"模型已下载到: {model_dir}")
