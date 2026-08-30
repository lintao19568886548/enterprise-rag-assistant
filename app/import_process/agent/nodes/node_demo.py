from app.core.logger import logger
from app.import_process.agent.node_base import NodeBase
from app.import_process.agent.state import create_default_state, ImportGraphState


class NodeDemo(NodeBase):

    name = "node_demo"

    def process(self, state: ImportGraphState) -> ImportGraphState:
        logger.info("节点处理逻辑")
        return state

if __name__ == "__main__":
    node_demo = NodeDemo()

    node_state = create_default_state(
        task_id= "task_001",
        local_file_Path="d:/abc")
    node_demo(node_state)
