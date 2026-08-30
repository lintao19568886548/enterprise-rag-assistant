# test/import/fastapi/异步1.py
import asyncio


async def download_file(name):
    print(f"开始下载：{name}")
    await asyncio.sleep(2)  # 模拟下载耗时 2 秒
    print(f"下载完成：{name}")

asyncio.run(download_file("文件 1"))