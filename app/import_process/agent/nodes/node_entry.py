import os.path

from app.core.logger import logger
from app.import_process.agent.node_base import NodeBase
from app.import_process.agent.state import ImportGraphState, create_default_state


class NodeEntry(NodeBase):
    """
    节点: 入口节点 (EntryNode)
    为什么叫这个名字: 作为图的 Entry Point，负责接收外部输入并决定流程走向。
    未来要实现:
    1. 接收文件路径。
    2. 判断文件类型 (PDF/MD)。
    3. 设置 state 中的路由标记。
    """

    # 覆盖基类的 name 属性，标识节点名称
    name: str = "node_entry"

    def process(self, state: ImportGraphState) -> ImportGraphState:
        """
        必要参数：
        - 必须包含 task_id(任务ID)
        - local_file_path(原始文件路径)
        - local_dir(输出文件的放置路径)
        更新参数：
        - is_pdf_read_enabled/is_md_read_enabled
        - pdf_path/md_path
        - file_title

        :param state: 工作流状态对象(包含入参的设置)
        :return: 更新后的状态对象(出参发生了改变)
        """

        # 1、参数校验
        document_path = state.get("local_file_path", "")
        if not document_path:
            logger.error("核心参数local_file_path缺失")
            return state

        # 2、根据文件后缀判断文件类型，设置对应的状态值
        if document_path.endswith(".pdf"):
            logger.info(f"PDF文件，{document_path} 开启PDF解析流程")
            state["is_pdf_read_enabled"] = True
            state["pdf_path"] = document_path
        elif document_path.endswith(".md"):
            logger.info(f"Markdown文件，{document_path} 开启Markdown解析流程")
            state["is_md_read_enabled"] = True
            state["md_path"] = document_path
        else:
            logger.warning(f"不支持的文件类型: {document_path}")

        # 3、根据文件路径获取不包含后缀的文件名
        file_name = os.path.basename(document_path)
        state["file_title"] = os.path.splitext(file_name)[0]
        logger.info(f"文件标题提取完成: {state['file_title']}")

        return state

if __name__ == "__main__":
    node_entry = NodeEntry()
    node_state = create_default_state(
        task_id="task_001",
        local_file_path="d:/abc.md",
        local_dir="d:/output"
    )
    # node_state_final = node_entry.process(node_state) #没有增强的版本
    node_state_final = node_entry(node_state) #增强的版本
