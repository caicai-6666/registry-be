"""同平台源文件身份的精确拦截，不依赖向量或智能体判断。"""

from app.model.resource import StoredResource


def active_source_reason(resource: StoredResource) -> str:
    if resource.source_id is None:
        raise ValueError("源文件 ID 缺失，不能构造身份冲突理由")
    # SQLite pending 申请没有正式资源 ID；MySQL 中存在即表示已入库。
    state = "待入库审核" if resource.resource_id is None else "已入库"
    return f"同平台、同 source_id 的文件已存在，当前处于{state}状态，不能重复提交入库申请。"
