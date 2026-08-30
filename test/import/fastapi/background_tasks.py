import uvicorn
from fastapi import BackgroundTasks, FastAPI
import time
from app.core.logger import logger

app = FastAPI()

# 定义一个模拟的耗时任务
def write_log(message: str):

    while True:
        print("正在执行任务...")
        time.sleep(1)

@app.post("/send-notification/{email}")
async def send_notification(email: str, background_tasks: BackgroundTasks):
    # 1. 添加任务到后台队列
    background_tasks.add_task(write_log, f"Notification sent to {email}")
    # 2. 立即返回响应给用户，不需要等待 write_log 执行完毕
    return {"message": "Notification sent in the background"}


#####################################################################################
# 在这启动可以进行断点调试
if __name__ == "__main__":
    """服务启动入口：本地开发环境直接运行"""
    logger.info("File Import Service 服务启动中...")
    # 启动uvicorn服务，绑定本地IP和8000端口，关闭自动重载（生产环境建议用workers多进程）
    uvicorn.run(
        app=app,
        host="127.0.0.1",  # 仅本地访问，生产环境改为0.0.0.0（允许所有IP访问）
        port=8000  # 服务端口
    )