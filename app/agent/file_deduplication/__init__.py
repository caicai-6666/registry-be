"""一对文件的判重子图及其输入输出契约。"""

from app.agent.file_deduplication.state import ComparisonFile, FileDeduplicationInput, FileDeduplicationResult
from app.agent.file_deduplication.workflow import build_file_deduplication_workflow

__all__ = ["ComparisonFile", "FileDeduplicationInput", "FileDeduplicationResult", "build_file_deduplication_workflow"]
