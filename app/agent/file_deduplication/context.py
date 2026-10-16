"""模型客户端是运行依赖，不进入文件比较的输入输出状态。"""

from dataclasses import dataclass
from typing import Callable

from app.config.file_deduplication import FileDeduplicationSettings
from app.infrastructure.mllm import MLLMClient


@dataclass(frozen=True)
class FileDeduplicationContext:
    mllm: MLLMClient | None = None
    settings: FileDeduplicationSettings | None = None
    # 每次会话独立生成完整审计，不把并发会话写进共享可变列表。
    audit_sink: Callable[[list[dict]], None] | None = None
