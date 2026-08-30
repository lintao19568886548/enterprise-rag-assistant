import asyncio
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()
app.add_middleware(
    CORSMiddleware,        # 启用跨域中间件
    allow_origins=["*"],   # 允许所有来源（任何网页都能调用）
    allow_methods=["*"],   # 允许所有请求方式（GET/POST等）
    allow_headers=["*"],   # 允许所有请求头
)

# 新增：接口接收session_id参数
@app.get("/stream/{session_id}")
async def stream_by_session(session_id: str):

    async def event_generator():
        for i in range(5):
            # 按session_id定制消息
            yield f"data: 会话{session_id} - 第{i+1}条消息\n\n"
            await asyncio.sleep(1)

    if session_id == "123":
        return StreamingResponse(event_generator(), media_type="text/event-stream")
    else:
        return {"message": "无效的会话ID"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)