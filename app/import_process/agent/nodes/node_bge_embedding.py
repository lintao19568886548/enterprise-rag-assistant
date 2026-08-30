import json
import os
from pathlib import Path
from typing import List, Dict

from app.core.logger import logger
from app.import_process.agent.node_base import NodeBase
from app.import_process.agent.state import ImportGraphState, create_default_state
from app.lm.embedding_utils import get_bge_m3_ef, generate_embeddings


class NodeBgeEmbedding(NodeBase):
    """
    节点: 向量化 (node_bge_embedding)
    为什么叫这个名字: 使用 BGE-M3 模型将文本转换为向量 (Embedding)。
    """

    # 覆盖基类的 name 属性，标识节点名称
    name: str = "node_bge_embedding"

    def process(self, state: ImportGraphState) -> ImportGraphState:
        """
        LangGraph核心节点：BGE-M3文本向量化处理
        主流程（串行执行，全流程异常隔离）：
            1. 输入校验：验证chunks有效性，核心数据缺失则终止当前节点
            2. 批量向量化：分批拼接文本、生成双向量，为切片绑定向量字段
            3. 状态更新：将带向量的chunks更新回全局状态，供下游Milvus入库节点使用

        必要参数：task_id、chunks
        更新参数：chunks字段新增dense_vector/sparse_vector

        :param state: 工作流状态对象
        :return: 更新后的状态对象
        """

        # 步骤1：输入数据校验
        texts_to_embed = self._step_1_validate_input(state)

        # 步骤2：批量生成双向量，为切片绑定向量字段
        output_data = self._step_2_generate_embeddings(texts_to_embed)

        # 步骤3：更新全局状态，将带向量的chunks回传下游
        state['chunks'] = output_data
        logger.info(f"--- BGE-M3 向量化处理完成，共处理 {len(output_data)} 条文本切片 ---")

        return state

    def _step_2_generate_embeddings(self, texts_to_embed: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """
        向量化核心步骤2：批量生成稠密/稀疏双向量
        核心逻辑：
            1. 文本拼接：item_name（商品名）+ 换行 + content（切片内容）
            2. 批量调用：传入拼接后的文本，生成批量双向量
            3. 向量绑定：为每个切片复制原数据，新增dense_vector/sparse_vector字段
        参数：
            texts_to_embed: 文本切片列表
        返回：
            List[Dict[str, str]] - 带向量字段的文本切片列表
        关键配置：
            batch_size: 每批处理batch_size条，可根据服务器显存大小调整
        """

        # 1、获取或设置一些初始变量
        #返回结果
        output_data = []
        #批处理大小
        batch_size = 5
        # 总共要处理的片段的数量
        total = len(texts_to_embed)

        # 2、开始进行批处理
        for i in range(0, total, batch_size):

            # 2.1 获取本次批处理列表
            batch_texts = texts_to_embed[i:i+batch_size]

            # 2.2 计算当前循环批处理的数据是哪条
            start_idx = i + 1
            end_idx = min(i + batch_size, total)

            # 2.3 拼接模型输入文本
            input_texts = []
            for doc in batch_texts:
                item_name = doc["item_name"]
                content = doc["content"]
                # 拼接 item_name 和 content
                text = f"{item_name}\n{content}"
                input_texts.append(text)

            # 2.4 调用向量模型封装好的函数批量生成双向量
            docs_embeddings = generate_embeddings(input_texts)
            if not docs_embeddings:
                error_msg = f"第{start_idx} - {end_idx}条切片，无返回结果"
                logger.error(error_msg)
                raise  RuntimeError(error_msg)

            # 2.5 获取向量
            for j, doc in enumerate(batch_texts):

                # 浅拷贝，避免直接修改state的值
                item = doc.copy()
                item["dense_vector"] = docs_embeddings["dense"][j]
                item["sparse_vector"] = docs_embeddings["sparse"][j]

                #组装带向量的切面数据
                output_data.append(item)


            logger.info(f"第{start_idx} - {end_idx} 条切片：双向量生成成功")


        return output_data


    def _step_1_validate_input(self, state: ImportGraphState) -> List[Dict]:
        """
        向量化前置步骤1：输入数据有效性校验
        核心作用：
            1. 从全局状态提取待向量化的chunks切片列表
            2. 严格校验chunks类型和非空性，无有效数据则终止向量化
        参数：
            state: ImportGraphState - 流程全局状态对象
        返回：
            List[Dict] - 校验通过的文本切片列表
        异常：
            若chunks非列表/为空，抛出ValueError，终止当前向量化流程
        """

        # 1、从状态中提取切片数据
        texts_to_embed = state.get("chunks")

        # 2、校验：必须是非空列表，否则无法进行向量化
        if not isinstance(texts_to_embed, list) or not texts_to_embed:
            logger.error("向量化输入校验失败：chunks字段为空或非有效列表")
            raise ValueError("错误: 无有效文本切片数据，无法执行向量化处理")

        logger.info(f"向量化输入校验通过，待处理文本切片数量：{len(texts_to_embed)}")
        return texts_to_embed

if __name__ == "__main__":

    # 获取项目所在路径
    from app.import_process.agent.state import create_default_state
    from app.utils.path_util import PROJECT_ROOT

    # 组装文件的绝对路径（使用 Path 对象，支持 read_text 方法）
    chunks_path = Path(PROJECT_ROOT) / "output/hak180产品安全手册/chunks_with_item_name.json"
    # 读取切片
    chunks_json = chunks_path.read_text(encoding="utf-8")
    # 将json字符串chunks转成列表
    chunks = json.loads(chunks_json)
    # 当前节点图状态初始值
    init_state = create_default_state(
        task_id="task_001",
        chunks=chunks
    )
    # 执行节点的业务调用
    node_bge_embedding = NodeBgeEmbedding()
    final_state = node_bge_embedding(init_state)


    #备份
    # 组装文件的绝对路径
    json_path = os.path.join(PROJECT_ROOT, "output", "hak180产品安全手册", "chunks_with_vector.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(final_state["chunks"], f, ensure_ascii=False, indent=2)
    logger.info(f"Chunk结果备份成功，备份文件路径：{json_path}")