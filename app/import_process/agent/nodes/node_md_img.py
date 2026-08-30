import os

import base64
import re
from collections import deque
from pathlib import Path
from typing import Tuple, List, Dict

from langchain_core.messages import HumanMessage
from minio import Minio
from minio.deleteobjects import DeleteObject

from app.conf.lm_config import lm_config
from app.conf.minio_config import minio_config
from app.core.load_prompt import load_prompt
from app.import_process.agent.node_base import NodeBase
from app.import_process.agent.state import ImportGraphState, create_default_state
from app.core.logger import logger
from app.lm.lm_utils import get_llm_client
from app.utils.rate_limit_utils import apply_api_rate_limit


class NodeMdImg(NodeBase):
    """
    节点: 图片处理 (node_md_img)
    为什么叫这个名字: 处理 Markdown 中的图片资源 (Image)。
    未来要实现:
    1. 扫描 Markdown 中的图片链接。
    2. 将图片上传到 MinIO 对象存储。
    3. (可选) 调用多模态模型生成图片描述。
    4. 替换 Markdown 中的图片链接为 MinIO URL。
    """

    # 覆盖基类的 name 属性，标识节点名称
    name: str = "node_md_img"

    def process(self, state: ImportGraphState) -> ImportGraphState:
        """
        MD文件图片处理核心节点
        核心流程：
        1. 获取MD内容、文件路径、图片文件夹路径
        2. 扫描图片文件夹，筛选MD中实际引用的支持格式图片
        3. 调用多模态大模型为图片生成内容摘要
        4. 将图片上传至MinIO，替换MD中本地图片路径为MinIO访问URL，并填充图片摘要
        5. 备份原MD文件，保存处理后的新MD文件并更新状态

        必要参数：task_id、md_path、md_content
        更新参数：md_path、md_content

        :param state: 工作流状态对象
        :return: 更新后的状态对象
        """

        # 步骤1：初始化数据，获取MD核心信息
        md_content, md_path_obj, images_dir = self._step_1_get_content(state)

        # 无图片文件夹，直接跳过图片处理逻辑
        if not images_dir.exists():
            logger.info(f"图片文件夹不存在，跳过图片处理：{images_dir.absolute()}")
            return state

        # 步骤2：扫描并筛选MD中引用的图片
        target_images = self._step_2_scan_images(md_content, images_dir)
        if not target_images:
            logger.info("未检测到MD中引用的支持格式图片，跳过后续处理")
            return state

        # 步骤3：调用多模态大模型生成图片摘要
        summaries = self._step_3_generate_summaries(md_path_obj.stem, target_images)

        # 步骤4：上传图片至MinIO，替换MD图片路径并填充摘要
        new_md_content = self._step_4_upload_and_replace(md_path_obj.stem, target_images, summaries, md_content)


        # 步骤5：备份并保存新MD文件，更新状态中的文件路径
        new_md_file_name = self._step_5_backup_new_md_file(state['md_path'], new_md_content)

        # 步骤6：更新state状态值
        state["md_content"] = new_md_content
        state["md_path"] = new_md_file_name

        return state

    def _step_5_backup_new_md_file(self, origin_md_path: str, md_content: str) -> str:
        """
        步骤5：将处理后的MD内容保存为新文件（原文件不变，避免数据丢失）
        新文件命名规则：原文件名 + _new.md（如test.md → test_new.md）
        :param origin_md_path: 原始MD文件完整路径
        :param md_content: 处理后的新MD内容
        :return: 新MD文件的完整路径
        """

        new_md_file_name = os.path.splitext(origin_md_path)[0] + "_new.md"
        with open(new_md_file_name, "w", encoding="utf-8") as f:
            f.write(md_content)

        logger.info(f"已保存处理后的MD文件，新文件是：{new_md_file_name}")
        return new_md_file_name

    def _step_4_upload_and_replace(self, doc_stem: str, target_images: List[Tuple[str, str, Tuple[str, str]]],
                                   summaries: Dict[str, str], md_content: str) -> str:
        """
        步骤4：清理MinIO旧目录 → 批量上传新图片 → 合并摘要和URL → 替换MD内容并存为新文档
        :param doc_stem: 文档文件名（不含后缀），作为MinIO上传子目录名（按文档隔离）
        :param target_images: 待处理图片列表，元素为(图片文件名, 图片完整路径, 图片上下文)
        :param summaries: 图片摘要字典，键：图片文件名，值：内容摘要
        :param md_content: 原始MD文件内容
        :return: 图片引用替换后的新MD内容
        """

        # 1、获取MinIO客户端
        from app.clients.minio_utils import get_minio_client
        minio_client = get_minio_client()

        if minio_client is None:
            logger.info("MinIO未启用，图片将由问答服务的本地 /images 路由提供")
            urls = {
                image_file: f"http://127.0.0.1:8001/images/{image_file}"
                for image_file, _, _ in target_images
            }
            image_info = self._merge_summary_and_url(summaries, urls)
            return self._process_md_file(md_content, image_info)

        # 2、获取MinIO的上传目录
        minio_img_dir = minio_config.minio_img_dir
        # object name
        upload_dir = f"{minio_img_dir}/{doc_stem}".replace(" ", "")

        # 步骤1：清理该文档对应的MinIO旧目录
        self._clean_minio_directory(minio_client, upload_dir)

        # 步骤2：批量上传图片至MinIO，获取URL映射
        urls = self._upload_images_batch(minio_client, upload_dir, target_images)

        # 步骤3：合并图片摘要和URL，过滤上传失败的图片
        image_info = self._merge_summary_and_url(summaries, urls)

        # 步骤4：替换MD内容中的本地图片引用为MinIO远程引用
        md_content = self._process_md_file(md_content, image_info)

        return md_content


    def _process_md_file(self, md_content: str, image_info: Dict[str, Tuple[str, str]]) -> str:
        """
        核心功能：替换MD内容中的本地图片引用为MinIO远程引用
        替换规则：![原描述](本地路径) → ![图片摘要](MinIO访问URL)
        :param md_content: 原始MD文件内容
        :param image_info: 合并后的图片信息字典，键：图片文件名，值：(摘要, URL)
        :return: 替换后的新MD内容

        ![summary](url)
        """
        for image_file, (summary, new_url) in image_info.items():
            pattern = re.compile(r"!\[.*?\]\(.*?" + re.escape(image_file) + r".*?\)")
            md_content = pattern.sub(lambda m : f"![{summary}]({new_url})", md_content)
            logger.info(f"完成图片引用的替换：{image_file} || {new_url} || {summary} ")

        logger.info(f"MD文件图片引用替换完成，共{len(image_info)}张图片")
        return md_content

    def _merge_summary_and_url(self, summaries: Dict[str, str], urls: Dict[str, str]) -> Dict[str, Tuple[str, str]]:
        """
        合并图片摘要字典和URL字典，过滤掉上传失败无URL的图片
        :param summaries: 图片摘要字典，键：图片文件名，值：内容摘要
        :param urls: 图片URL字典，键：图片文件名，值：MinIO访问URL
        :return: 合并后的图片信息字典，键：图片文件名，值：(摘要, URL)元组
        """

        image_info = {}
        for image_file, summary in summaries.items():
            if url := urls.get(image_file):
                image_info[image_file] = (summary, url)
        logger.info(f"图片摘要与url合并完成，共{len(image_info)}条图片信息")
        return image_info

    def _upload_to_minio(self, minio_client: Minio, local_path: str, object_name: str) -> str | None:
        """
        将单张本地图片上传至MinIO对象存储，并返回公网可访问URL
        :param minio_client: 初始化完成的MinIO客户端对象
        :param local_path: 图片本地完整路径
        :param object_name: MinIO中要存储的对象名称
        :return: 图片MinIO访问URL（上传失败返回None）
        """
        try:

            logger.info(f"开始上传图片至MinIO：本地路径：{local_path}，MinIO对象：{object_name}")

            # 上传图片至MinIO
            minio_client.fput_object(
                bucket_name=minio_config.bucket_name,
                object_name=object_name,
                file_path=local_path,
                # MIME 类型
                content_type=f"image/{os.path.splitext(local_path)[1][1:]}",
            )

            # http://192.168.100.101:9000/knowledge-base-files/upload-images/hak180产品安全手册/name.jpg
            protocol  = "https" if minio_config.minio_secure else "http"
            img_url = f"{protocol}://{minio_config.endpoint}/{minio_config.bucket_name}/{object_name}"

            logger.info(f"图片上传成功，访问URL：{img_url}")
            return img_url
        except Exception as e:
            logger.error(f"图片上传MinIO失败：{local_path}，错误信息：{str(e)}")
            return None

    def _upload_images_batch(self, minio_client: Minio, upload_dir: str,
                             target_images: List[Tuple[str, str, Tuple[str, str]]]) -> Dict[str, str]:
        """
        批量上传待处理图片至MinIO，返回图片文件名与访问URL的映射关系
        :param minio_client: 初始化完成的MinIO客户端对象
        :param upload_dir: MinIO上传根目录
        :param target_images: 待处理图片列表，元素为(图片文件名, 图片完整路径, 图片上下文)
        :return: 图片URL字典，键：图片文件名，值：MinIO访问URL
        """
        urls = {}

        for img_file, img_path, _ in target_images:
            object_name = f"{upload_dir}/{img_file}"
            logger.info(f"上传图片：{object_name}")

            # img_url = self._upload_to_minio(minio_client, img_path, object_name)
            # if img_url is not None:
            #     urls[img_file] = img_url
            #海象运算符 :=
            if img_url := self._upload_to_minio(minio_client, img_path, object_name) :
                urls[img_file] = img_url

        logger.info(f"图片批量上传完成，成功上传了{len(urls)} 张图片")
        return urls

    def _clean_minio_directory(self, minio_client: Minio, prefix: str) -> None:
        """
        幂等性清理MinIO指定目录下的所有旧文件，防止垃圾文件堆积
        幂等性：多次调用结果一致，无文件时不报错
        :param minio_client: 初始化完成的MinIO客户端对象
        :param prefix: MinIO目录前缀（要清理的目录路径）
        """
        try:
            # 获取指定目录下的所有对象列表
            objects_to_delete = minio_client.list_objects(
                bucket_name=minio_config.bucket_name,
                prefix=prefix,
                recursive=True,
            )

            # delete_list = []
            # for obj in objects_to_delete:
            #     delete_list.append(DeleteObject(obj.object_name))

            # 列表推导式：组装待删除图片列表
            delete_list = [DeleteObject(obj.object_name) for obj in objects_to_delete]
            # 判断当前路径是否有需要删除的对象
            if delete_list:
                logger.info(f"MinIO目录清理开始：{prefix}，待清理文件数：{len(delete_list)}")

                # 根据对象列表批量删除对象
                errors = minio_client.remove_objects(minio_config.bucket_name, delete_list)

                # 如果删除过程有错误信息，记录日志
                for error in errors:
                    logger.warning(f"MinIO文件删除失败：{error}")

                logger.info(f"MinIO文件清理完成：{prefix}")
            else:
                logger.info(f"MinIO目录无需清理：{prefix}")

        except Exception as e:
            logger.error(f"MinIO目录清理失败：{prefix}，错误信息：{str(e)}")


    def _step_3_generate_summaries(self, doc_stem: str, target_images: List[Tuple[str, str, Tuple[str, str]]]) -> Dict[str, str]:
        """
        步骤3：批量为待处理图片生成内容摘要，带API速率限制防止触发大模型限流
        :param doc_stem: 文档文件名（不含后缀），作为大模型prompt上下文
        :param targets: 待处理图片列表，元素为(图片文件名, 图片完整路径, 图片上下文)
        :param requests_per_minute: 每分钟最大API请求数，默认9次（按大模型限制调整）
        :return: 图片摘要字典，键：图片文件名，值：图片内容摘要
        """
        summaries = {}

        # 1、外部初始化双端队列，用于API速率限制，跨循环复用
        request_deque = deque()

        # 2、循环处理图片
        for image_file, image_path, context in target_images:

            # 2.1、速率限制
            apply_api_rate_limit(request_deque, max_requests=20, window_seconds=60)

            # 2.2、调用大模型生成图片摘要
            logger.info(f"开始生成图片摘要：{image_file}")
            summaries[image_file] = self._summarize_image(image_path, root_folder=doc_stem, image_content=context)

        logger.info(f"图片摘要全部生成完成，共{len(summaries)}张图片")
        return summaries


    def _summarize_image(self, image_path: str, root_folder: str, image_content: Tuple[str, str]) -> str:
        """
        调用多模态大模型生成图片内容摘要（适配LangChain工具类，复用项目统一LLM客户端）
        生成的摘要用于Markdown图片标题，严格控制50字以内中文描述
        :param image_path: 图片本地完整路径
        :param root_folder: 文档所属文件夹/主名，为大模型提供上下文
        :param image_content: 图片在MD中的上下文元组，格式(上文文本, 下文文本)
        :return: 图片内容摘要（异常时返回默认值"图片描述"）
        """

        # 1、加载并渲染提示词
        prompt_text = load_prompt(
            name="image_summary",
            root_folder=root_folder,
            image_content=image_content
        )

        # 2、将图片进行base64编码
        with open(image_path, "rb") as img_file:
            base64_image = base64.b64encode(img_file.read()).decode("utf-8")

        # 3、构建LangChain的message对象
        messages = [
            HumanMessage(
                content=[
                    {
                        "type": "text",
                        "text": prompt_text
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        }
                    }
                ]
            )
        ]

        # 4、获取VLM客户端
        vlm_client = get_llm_client(model=lm_config.lv_model)

        # 5、调用大模型
        response = vlm_client.invoke(messages)

        # 6、解析模型响应
        summary = response.content.strip().replace("\n", "")
        logger.info(f"图片摘要生成成功：{image_path}，摘要：{summary}")
        return summary


    def _step_2_scan_images(self, md_content: str, images_dir: Path) -> List[Tuple[str, str, Tuple[str, str]]]:
        """
        扫描图片文件夹，过滤出「支持格式+MD中实际引用」的图片，组装处理元数据
        :param md_content: MD文件完整内容
        :param images_dir: 图片文件夹路径对象
        :return: 待处理图片列表，每个元素为(图片文件名, 图片完整路径, 图片上下文)元组
        """

        # MinIO支持的图片格式集合（小写后缀，统一匹配标准）
        image_extensions = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}
        target_images = []

        # 1、遍历图片文件夹，筛选出支持的图片
        # for image_file in images_dir.iterdir():  #os.listdir(images_dir)
        #     if image_file.is_file() and image_file.suffix.lower() in image_extensions:
        for image_file in os.listdir(images_dir):

            # 1.1、过滤无效后缀
            # 123.jpg -> 123  .jpg ->
            file_ext = os.path.splitext(image_file)[1].lower()
            if file_ext not in image_extensions:
                logger.warning(f"图片文件格式不支持, 跳过：{image_file}")
                continue

            # 1.2、组装完整的图片路径字符串
            img_path = str(images_dir / image_file)

            # 1.3、查找图片在MD中的上下文
            context = self._find_image_in_md(md_content, image_file)
            if not context:
                logger.warning(f"图片未在MD中找到引用，跳过：{image_file}")
                continue

            # 1.4、
            target_images.append((image_file, img_path, context))
            logger.info(f"当前图片元数据组装完成：{image_file}，并已加入图片列表")

        logger.info(f"图片文件夹扫描完成，共{len(target_images)}张图片")
        return target_images

    def _find_image_in_md(self, md_content: str, image_file: str, context_len: int = 100) -> Tuple[str, str]:
        """
        在MD文件中查找图片在MD中的上下文
        :param md_content: MD文件内容
        :param image_file: 含后缀的图片文件名
        :return: 图片上下文元组，包含图片在MD中的上下文；图片没有引用则返回None
        """

        # 1、定义正则表达式
        # ![描述](images/文件名.扩展名)
        # r"字符串"：不要将其中的特殊符号进行转义
        # re.escape 转义图片文件名中的特殊字符，避免正则语法错误
        # .* 贪婪匹配 .*? 非贪婪匹配
        pattern = re.compile(r"!\[.*?\]\(.*?" + re.escape(image_file) + r".*?\)")

        # 2、找到一个匹配项立即返回
        match = pattern.search(md_content)
        if not match:
            return None # 无匹配项
        # for match in pattern.finditer(md_content):
        #     print(match)
        #
        # 3、截取匹配位置的上文和下文，注意不要索引越界
        start, end = match.span()
        pre_text = md_content[max(0, start - context_len):start]
        post_text = md_content[end:min(len(md_content), end + context_len)]
        # 打印上文和下文
        logger.info(f"图片{image_file}的上文：{pre_text}")
        logger.info(f"图片{image_file}的下文：{post_text}")

        # 4、返回图片上下文
        return pre_text, post_text

    def _step_1_get_content(self, state: ImportGraphState) -> Tuple[str, Path, Path]:
        """
        从全局状态中提取并初始化MD处理所需核心数据
        :param state: 导入流程全局状态对象
        :return: 三元组(MD文件内容, MD文件路径对象, 图片文件夹路径对象)
        :raise FileNotFoundError: 当状态中无有效MD文件路径时抛出
        """

        # 获取路径
        md_path = state.get("md_path", "").strip()

        # 1、参数的非空校验
        if not md_path:
            raise ValueError("核心参数md_path缺失")

        # 2、路径转换
        md_path_obj = Path(md_path)

        # 3、检查PDF文件的有效性
        if not md_path_obj.exists():
            raise ValueError(f"MD文件不存在，绝对路径: {md_path_obj.absolute()}")

        # 4、优先使用state中的md_content, 如果为空则从文件中读取
        md_content = state.get("md_content", "")
        if not md_content:
            md_content = md_path_obj.read_text(encoding="utf-8")
            state["md_content"] = md_content #赋值到状态对象中
            logger.info(f"从文件读取MD内容完成，文件大小：{len(md_content)} 字符")
        else :
            md_content = state["md_content"]
            logger.info(f"从状态中获取MD内容完成，文件大小：{len(md_content)} 字符")

        # 5、组装图片文件夹路径：images
        images_dir = md_path_obj.parent / "images"

        return md_content, md_path_obj, images_dir

if __name__ == "__main__":

    import os
    # 获取项目所在路径
    from app.utils.path_util import PROJECT_ROOT


    # 组装文件路径
    md_name= os.path.join("output/hak180产品安全手册", "hak180产品安全手册.md")
    # 组装文件的绝对路径
    md_path = os.path.join(PROJECT_ROOT, md_name)
    # 组装输出路径
    local_dir = os.path.join(PROJECT_ROOT, "output")

    # 当前节点图状态初始值
    init_state = create_default_state(
        task_id="task_001",
        md_path=md_path,
        md_content=""
    )

    # 执行节点的业务调用
    node_md_img = NodeMdImg()
    final_state = node_md_img(init_state)
