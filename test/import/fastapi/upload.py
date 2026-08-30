# test/fastapi/upload.py
from fastapi import FastAPI, File,UploadFile,HTTPException
from fastapi.responses import JSONResponse
import os

app = FastAPI()

UPLOAD_FOLDER = 'uploads'
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.post("/upload",summary="单个文件上传接口")
async def upload(
    file: UploadFile = File(
    ..., # 必填参数
    description="需要上传的文件（支持图片/文档等)",
    alias="upload_file", #前端参数的别名，默认文件名是file
    media_type="application/octet-stream"), #接受任何类型的文件（图片、文档、视频等）
    remark:str = None #可选参数
):

    try:
        # 1. 校验文件类型（示例：只允许上传图片）
        ALLOWED_TYPES = ["image/jpeg", "image/png", "image/gif"]
        if file.content_type not in ALLOWED_TYPES:
            raise HTTPException(
                status_code=400,
                detail=f"仅支持上传{ALLOWED_TYPES}类型的文件，当前文件类型：{file.content_type}")

        file_path = os.path.join(UPLOAD_FOLDER, file.filename)
        CHUNK_SIZE = 1024 * 1024  # 1MB分块
        with open(file_path, "wb") as f:
            # 分块读取大文件（避免内存溢出）
            while True:
                chunk = await file.read(CHUNK_SIZE)  # 每次读1MB
                if not chunk:
                    break
                f.write(chunk)
            """
            await的必要性：file.read()是异步函数，必须用await才能拿到文件内容，否则程序报错；
            分块读取的核心：给read()传一个字节数（如 1MB），循环读取 + 写入，避免大文件占满内存；
            用法选择：小文件可以用await file.read()一次性读取，大文件必须分块读取；
            """
        return JSONResponse(
            status_code=200,
            content={
                "code": 0,
                "msg": "文件上传成功",
                "data": {
                    "filename": file.filename,
                    "content_type": file.content_type,
                    "file_size": f"{file.size} 字节",  # 文件大小
                    "save_path": file_path,
                    "remark": remark or "无备注"
                }
            }
        )
    except Exception as e:
        # 异常捕获：返回友好的错误信息
        raise HTTPException(
            status_code=500,
            detail=f"文件上传失败：{str(e)}"
        )

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)