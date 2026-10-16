"""将已确认重复文件的身份与智能体理由组织为 Markdown 审核意见。"""

import re


def build_duplicate_rejection_note(source_id: str, reason: str) -> str:
    if not isinstance(source_id, str) or not source_id.strip() or len(source_id) > 128:
        raise ValueError("重复文件 source_id 不能为空且最多 128 字符")
    if any(ord(character) < 32 or ord(character) == 127 for character in source_id):
        raise ValueError("重复文件 source_id 不能包含换行或控制字符")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("拒绝审核意见必须包含智能体给出的重复理由")

    # 动态选择代码跨度分隔符，避免 source_id 中的反引号破坏 Markdown。
    runs = re.findall(r"`+", source_id)
    fence = "`" * (max(map(len, runs), default=0) + 1)
    value = source_id
    if runs or source_id != source_id.strip():
        value = f" {source_id} "
    note = (
        "## 入库申请拒绝\n\n"
        "经自动审核，当前文件与已有文件重复，拒绝入库。\n\n"
        "### 重复文件\n\n"
        f"- **文件 source_id：** {fence}{value}{fence}\n\n"
        "### 重复理由\n\n"
        f"{reason.strip()}"
    )
    # 中间件 note 最多 10000 字符，不静默裁剪模型理由或发出必然被拒绝的请求。
    if len(note) > 10000:
        raise ValueError("完整 Markdown 审核意见超过中间件 10000 字符上限")
    return note
