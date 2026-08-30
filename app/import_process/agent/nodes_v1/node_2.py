import sys

from app.core.logger import logger
from app.import_process.agent.state import ImportGraphState


def node_1(state: ImportGraphState) -> ImportGraphState:
    """
    节点1：样例节点
    """

    node_name = sys._getframe().f_code.co_name
    logger.info("节点开始运行" + node_name)
    # 节点运行时状态跟踪：开始 TODO

    try:

        #节点逻辑 TODO

        # 节点运行时状态跟踪：结束 TODO
        logger.info("节点运行结束" + node_name)

        return  state
    except Exception as e:
        logger.exception(f"节点运行异常：{node_name}")
        raise e