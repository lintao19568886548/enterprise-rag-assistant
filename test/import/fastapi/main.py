import uvicorn
from fastapi import FastAPI
from app.core.logger import logger
app = FastAPI()
@app.get("/", summary="第一个测试")
async def root():
    return {"message": "Hello World"}

# 访问 http://127.0.0.1:8000/items/5?q=somequery
# item_id: 路径参数 (自动转为 int)
# q: 查询参数 (可选，默认 None)
@app.get("/items/{item_id}", summary="获取指定参数")
async def read_item(item_id: int, q: str | None = None):
    return {"item_id": item_id, "q": q}


# 接收? skip=? & limit = ?
@app.get("/items/", summary="分页")
def read_item(skip: int = 0, limit: int = 10):
    return {"skip": skip, "limit": limit}

#############################################################################

# 展示如何定义数据结构，FastAPI 会自动进行类型检查和错误提示。
from pydantic import BaseModel

# 定义数据模型
# FastAPI 要求所有自定义数据模型必须继承 BaseModel
class Item(BaseModel):
    name: str
    price: float
    is_offer: bool = None


# POST 请求接收 JSON 数据
@app.post("/items/", summary="类型检查")
def create_item(item: Item):
    # breakpoint()
    # item 已经是验证过的 Item 对象
    # 如果客户端传来的 price 是字符串 "abc"，FastAPI 会自动报错
    return {"name": item.name, "price": item.price, "is_offer": item.is_offer}


#####################################################################################

from fastapi import Header, Cookie

# 使用 Header 和 Cookie 类型注解获取请求头和 Cookie 数据。
@app.get("/header_cookie", summary="使用 Header 和 Cookie 类型注解")
def read_item(user_agent: str = Header(None), session_token: str = Cookie(None)):
    return {"User-Agent": user_agent, "Session-Token": session_token}


#####################################################################################
# 路由处理函数返回一个 Pydantic 模型实例，FastAPI 将自动将其转换为 JSON 格式，并作为响应发送给客户端：
@app.post("/items/return", summary="返回 Pydantic 模型实例")
def create_item(item: Item):
    return item

#使用 RedirectResponse 实现重定向，将客户端重定向到 /items/ 路由。
from fastapi.responses import RedirectResponse

@app.get("/redirect", summary="请求重定向")
def redirect():
    return RedirectResponse(url="/items/")

#使用 HTTPException 抛出异常，返回自定义的状态码和详细信息。
#以下实例在 item_id 为 42 会返回 404 状态码：
from fastapi import HTTPException

@app.delete("/items/{item_id}", summary="抛出异常")
def read_item(item_id: int):
    if item_id == 42:
        raise HTTPException(status_code=404, detail="Item 找不到")
    return {"item_id": item_id}

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