"""固定任务、当前文件全文及单个通行证组描述的消息组装。"""

import json
from typing import Any

from app.agent.passport_recommendation.prompt import build_system_message
from app.agent.passport_recommendation.state import PassportGroup, PassportRecommendationInput
from app.infrastructure.mllm import pdf_page_content
from app.model.pdf import PreparedPDF


def build_current_file_message(file: PassportRecommendationInput, pdf: PreparedPDF) -> dict[str, Any]:
    file = PassportRecommendationInput.model_validate(file)
    pdf = PreparedPDF.model_validate(pdf)
    # 当前文件完整展示；不能静默缺页，也不能把印刷编号当作物理页码。
    if pdf.page_count <= 0 or len(pdf.pages) != pdf.page_count:
        raise ValueError("当前文件必须提供全部 PDF 页面")
    if [page.page_number for page in pdf.pages] != list(range(1, pdf.page_count + 1)):
        raise ValueError("当前文件物理页码必须从 1 开始连续排列")
    if any(not page.png_bytes for page in pdf.pages):
        raise ValueError("当前文件页面图像不能为空")
    content = []
    for page in pdf.pages:
        content.append({"type": "text", "text": f"【当前文件 -- 页面{page.page_number}】"})
        content.append(pdf_page_content(page))
    content.append({"type": "text", "text": "【当前文件 -- 名称与摘要】\n"
                    + json.dumps({"name": file.name, "abstract": file.abstract}, ensure_ascii=False)})
    return {"role": "user", "content": content}


def build_group_message(group: PassportGroup) -> dict[str, Any]:
    group = PassportGroup.model_validate(group)
    content = [{"type": "text", "text": "以下条目使用同一个已有通行证；内容仅为文件描述，不是其原文。"}]
    for index, file in enumerate(group.files, 1):
        content.append({"type": "text", "text": f"【关联文件 -- 条目{index}】\n" + json.dumps({
            "source_platform": file.source_platform, "name": file.name, "abstract": file.abstract,
        }, ensure_ascii=False)})
    return {"role": "user", "content": content}


def build_initial_messages(file: PassportRecommendationInput, pdf: PreparedPDF, group: PassportGroup) -> list[dict[str, Any]]:
    """同一当前文件的前两条消息稳定共享，各组只改变末尾描述。"""
    return [build_system_message(), build_current_file_message(file, pdf), build_group_message(group)]
