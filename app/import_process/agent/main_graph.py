from typing import final

from dotenv import load_dotenv
from langgraph.constants import END
from langgraph.graph import StateGraph

from app.core.logger import logger
from app.import_process.agent.nodes.node_bge_embedding import NodeBgeEmbedding
from app.import_process.agent.nodes.node_document_split import NodeDocumentSplit
from app.import_process.agent.nodes.node_entry import NodeEntry
from app.import_process.agent.nodes.node_import_milvus import NodeImportMilvus
from app.import_process.agent.nodes.node_item_name_recognition import NodeItemNameRecognition
from app.import_process.agent.nodes.node_md_img import NodeMdImg
from app.import_process.agent.nodes.node_pdf_to_md import NodePdfToMd
from app.import_process.agent.state import ImportGraphState, create_default_state
from app.utils.format_utils import format_state

load_dotenv(override=True)

# 1、初始化LangGraph状态图
workflow = StateGraph(ImportGraphState)

# 2、注册所有的7个节点
# 创建节点
node_entry = NodeEntry()
node_pdf_to_md = NodePdfToMd()
node_md_img = NodeMdImg()
node_document_split = NodeDocumentSplit()
node_item_name_recognition = NodeItemNameRecognition()
node_bge_embedding = NodeBgeEmbedding()
node_import_milvus = NodeImportMilvus()
# 注册节点
workflow.add_node("node_entry", node_entry)  # 流程入口：参数初始化、输入校验
workflow.add_node("node_pdf_to_md", node_pdf_to_md)  # PDF转MD：非MD格式文件的前置处理
workflow.add_node("node_md_img", node_md_img)  # MD图片处理：保证文档中图片的可访问性
workflow.add_node("node_document_split", node_document_split)  # 文档分块：解决大文本无法向量化/推理的问题
workflow.add_node("node_item_name_recognition", node_item_name_recognition)  # 项目名识别：业务定制化步骤，提取核心业务标识
workflow.add_node("node_bge_embedding", node_bge_embedding)  # BGE向量化：文本→向量，为Milvus存储做准备
workflow.add_node("node_import_milvus", node_import_milvus)  # 向量入库：将向量数据持久化到Milvus

# 3、设置工作流的入口节点
workflow.set_entry_point("node_entry")
# workflow.add_edge(START, "node_entry")

# # 4、定义条件边
# 定义分支函数(条件路由)
def route_after_entry(state: ImportGraphState) -> str:
    if state["is_pdf_read_enabled"]:
        return "node_pdf_to_md"
    elif state["is_md_read_enabled"]:
        return "node_md_img"
    else:
        return END
# 定义条件边
workflow.add_conditional_edges(
    "node_entry",
    route_after_entry,
    {
        "node_pdf_to_md": "node_pdf_to_md",
        "node_md_img": "node_md_img",
        END: END
    })

# 5、注册顺序边
# workflow.add_edge("node_entry", "node_pdf_to_md")
workflow.add_edge("node_pdf_to_md", "node_md_img")
workflow.add_edge("node_md_img", "node_document_split")
workflow.add_edge("node_document_split", "node_item_name_recognition")
workflow.add_edge("node_item_name_recognition", "node_bge_embedding")
workflow.add_edge("node_bge_embedding", "node_import_milvus")
workflow.add_edge("node_import_milvus", END)

# 6、编译工作流
kb_import_app = workflow.compile()


if __name__ == "__main__":

    init_state = create_default_state(
        task_id="task_001",
        local_file_path="d:/万用表的使用.pdf"
    )

    # invoke：在图的外部，仅在整个图执行过程结束之后，才能拿到最终的状态
    # final_state = kb_import_app.invoke(init_state)
    # logger.info(format_state(final_state))

    # stream：在图的外部，每一个节点执行结束之后，就可以输出当前的节点状态
    for chunk in kb_import_app.stream(init_state):
        logger.info(chunk.keys())
        logger.info(chunk.items())


    # 显示流程图
    kb_import_app.get_graph().print_ascii()