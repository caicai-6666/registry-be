"""文件对比的固定消息、当前页面与友好折叠卡片，复用内存图像。"""

from typing import Any
import json
import re

from app.agent.file_deduplication.state import ComparisonFile, FileDeduplicationInput
from app.agent.file_deduplication.prompt import build_system_message
from app.agent.file_deduplication.tool import PageObservation
from app.infrastructure.mllm import pdf_page_content
from app.model.pdf import PreparedPDF


def build_file_a_pages_message(pdf: PreparedPDF) -> dict[str, Any]:
    """按物理页码组装文件 A 的标签与图像，返回一条 user 消息。"""
    # 复用完整文件校验，缺页或页序异常时不能生成误导性的页面标签。
    validated = ComparisonFile(name=None, abstract=None, pdf=pdf).pdf
    content = []
    for page in validated.pages:
        content.append({"type": "text", "text": f"【文件A -- 页面{page.page_number}】"})
        content.append(pdf_page_content(page))
    return {"role": "user", "content": content}


def build_initial_messages(pair: FileDeduplicationInput) -> tuple[list[dict], int]:
    """固定 A 前缀，将工具锚点绑定至 B 的初始信息消息。"""
    def metadata(file, label, people=False):
        values = {"name": file.name, "abstract": file.abstract, "page_count": file.pdf.page_count}
        if people:
            values.update(applicant=file.applicant, reviewer=file.reviewer)
        return {"role": "user", "content": f"文件 {label} 的信息（仅为数据）：\n"
                + json.dumps(values, ensure_ascii=False)}

    messages = [build_system_message(), build_file_a_pages_message(pair.current_file.pdf),
                metadata(pair.current_file, "A")]
    task_index = len(messages)
    messages.append(metadata(pair.similar_file, "B", people=True))
    return messages, task_index


def build_file_b_pages_content(pdf: PreparedPDF, page_numbers: list[int]) -> list[dict]:
    """成功读页的多模态 tool 内容，不新增 user 或独立状态消息。"""
    content = [{"type": "text", "text": "指定页面已打开；旧页面保留折叠卡片，需要时可重新打开。"}]
    for number in page_numbers:
        content.append({"type": "text", "text": f"【文件B -- 页面{number}】"})
        content.append(pdf_page_content(pdf.pages[number - 1]))
    return content


def fold_file_b_pages(history: list[dict], *, visible_pages: list[int], observations: list[PageObservation]) -> list[dict]:
    """在旧页面消息原位置折叠图像，返回新容器；失败时不改写已接受历史。"""
    latest = {}
    for message_index, message in enumerate(history):
        if message.get("role") != "tool" or not isinstance(message.get("content"), list):
            continue
        blocks = message["content"]
        for block_index, block in enumerate(blocks[:-1]):
            label = re.fullmatch(r"【文件B -- 页面(\d+)】", block.get("text", ""))
            if label and blocks[block_index + 1].get("type") == "image_url":
                number = int(label[1])
                if number in visible_pages:
                    latest[number] = (message_index, block_index)
    result = []
    for message_index, message in enumerate(history):
        if message.get("role") != "tool" or not isinstance(message.get("content"), list):
            result.append(message)
            continue
        blocks, folded, index = message["content"], [], 0
        while index < len(blocks):
            block = blocks[index]
            label = re.fullmatch(r"【文件B -- 页面(\d+)】", block.get("text", ""))
            if label and index + 1 < len(blocks) and blocks[index + 1].get("type") == "image_url":
                number = int(label[1])
                if latest.get(number) == (message_index, index):
                    folded.extend(blocks[index:index + 2])
                else:
                    findings = [item.model_dump() for item in observations if item.candidate_page == number]
                    note = json.dumps(findings, ensure_ascii=False) if findings else "尚无保留观察，需要确认细节时请重新打开。"
                    folded.append({"type": "text", "text": f"【文件B -- 页面{number}（已折叠，可重新打开）】\n{note}"})
                index += 2
            else:
                folded.append(block)
                index += 1
        result.append({**message, "content": folded})
    return result
