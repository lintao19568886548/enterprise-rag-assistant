# \test\import\fastapi\bg_task.py
from fastapi import BackgroundTasks, FastAPI
import time

app = FastAPI()

# 定义一个模拟的耗时任务
def write_log(message: str):

    while True:
        print("正在执行任务..." + message)
        time.sleep(1)

@app.post("/send-task/{email}")
async def send_task(email: str, background_tasks: BackgroundTasks):
    # 1. 添加任务到后台队列
    background_tasks.add_task(write_log, f"发送通知： {email}")
    # 2. 立即返回响应给用户，不需要等待 write_log 执行完毕
    return {"message": "通知任务已发送"}



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)

# uv run uvicorn test.import.fastapi.bg_task:app --reload