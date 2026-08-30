import asyncio
from fastapi import FastAPI, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

app = FastAPI()
app.add_middleware(
    CORSMiddleware,        # 启用跨域中间件
    allow_origins=["*"],   # 允许所有来源（任何网页都能调用）
    allow_methods=["*"],   # 允许所有请求方式（GET/POST等）
    allow_headers=["*"],   # 允许所有请求头
)

# 用异步队列存储每个会话的待推送数据
task_queues = {}

# 新增：定义请求体模型
class QueryRequest(BaseModel):
    query: str
    session_id: str

# 重构异步任务：往队列丢数据
async def long_task(session_id: str, query: str):
    # 为当前会话创建专属异步队列
    queue = asyncio.Queue()
    task_queues[session_id] = queue

    # 按查询词生成5条结果，每秒1条丢进队列
    for i in range(5):
        msg = f"【{query}】的第{i+1}段回答：xxx{i+1}"
        await queue.put(msg)  # 数据入队
        await asyncio.sleep(1)

    # 关键：放入结束标记，告诉SSE停止推送
    await queue.put(None)

# POST接口
@app.post("/submit_query")
async def submit_query(req: QueryRequest, background_tasks: BackgroundTasks):
    # 把查询词和会话ID传给后台任务
    background_tasks.add_task(long_task, req.session_id, req.query)
    return {"message": "任务已启动", "session_id": req.session_id}

# 简化SSE接口：从队列取数据
@app.get("/stream/{session_id}")
async def stream_result(session_id: str):
    async def event_generator():
        # 等待当前会话的队列创建（防止SSE比任务先启动）
        while session_id not in task_queues:
            await asyncio.sleep(0.1)
        queue = task_queues[session_id]

        # 循环取队列数据，有数据就推，收到结束标记就停
        while True:
            msg = await queue.get()  # 异步阻塞等待数据
            if msg is None:  # 收到结束标记，退出循环
                break
            yield f"data: {msg}\n\n"  # 推送SSE数据

    return StreamingResponse(event_generator(), media_type="text/event-stream")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8001)