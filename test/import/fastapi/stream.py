from fastapi import FastAPI
from fastapi.responses import StreamingResponse
import asyncio

app = FastAPI()

async def generate_stream():
    # 模拟流式输出（逐字返回）
    words = ["你", "好", "，", "这", "是", "流", "式", "响", "应"]
    for word in words:
        await asyncio.sleep(0.5)
        yield word.encode("utf-8")  # 流式输出需返回字节流

@app.get("/stream")
async def stream_response():
    return StreamingResponse(generate_stream(), media_type="text/plain")

from fastapi.responses import Response

@app.get("/custom")
def custom_response():
    # 返回二进制数据，指定自定义 MIME 类型
    return Response(
        content=b"custom binary data",
        media_type="application/octet-stream",
        status_code=200)