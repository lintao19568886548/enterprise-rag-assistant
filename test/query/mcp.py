# 在 Python 交互式环境中
from agents.mcp import MCPServerStreamableHttp
import inspect

# 查看 call_tool 方法的签名和文档
print(inspect.signature(MCPServerStreamableHttp.call_tool))
print(inspect.getsource(MCPServerStreamableHttp.call_tool))