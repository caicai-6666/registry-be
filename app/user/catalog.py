"""审核人员 YAML 的严格启动期加载；错误信息不包含凭据原文。"""

from pathlib import Path

import yaml
from pydantic import ValidationError

from app.user.model import ReviewerUserCatalog, ReviewerUserFile


class ReviewerUserCatalogError(ValueError):
    """人员文件无法形成可信快照。"""


class UniqueKeyLoader(yaml.SafeLoader):
    """避免 YAML 重复键被默认加载器静默覆盖。"""


def _unique_mapping(loader, node):
    mapping = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if key in mapping:
            raise ValueError("人员 YAML 不允许重复键")
        mapping[key] = loader.construct_object(value_node)
    return mapping


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def load_reviewer_user_catalog(path: Path) -> ReviewerUserCatalog:
    resolved_path = path.resolve()
    if not resolved_path.is_file() or resolved_path.suffix != ".yaml":
        raise ReviewerUserCatalogError(f"人员文件不存在或不是 .yaml 文件：{resolved_path}")
    try:
        raw_content = resolved_path.read_bytes()
        payload = yaml.load(raw_content.decode("utf-8"), Loader=UniqueKeyLoader)
        definition = ReviewerUserFile.model_validate(payload)
        return ReviewerUserCatalog.from_file(resolved_path, definition, raw_content)
    except (OSError, UnicodeError, yaml.YAMLError, ValidationError, ValueError, TypeError):
        # YAML/Pydantic 原始异常可能包含整条人员记录和密钥，禁止直接拼接或串联。
        raise ReviewerUserCatalogError(f"人员文件无法读取或不符合 Schema：{resolved_path}") from None
