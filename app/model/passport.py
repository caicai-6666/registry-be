"""文件通行证数组及数据库 JSON 编解码；兼容仅用于历史边界。"""

import json
from typing import Annotated

from pydantic import AfterValidator, Field, TypeAdapter


def _nonblank(value: str) -> str:
    if not value.strip():
        raise ValueError("通行证不能仅为空白")
    return value


PassportValue = Annotated[str, Field(strict=True, min_length=1, max_length=512), AfterValidator(_nonblank)]
Passports = Annotated[list[PassportValue], Field(strict=True)]
_PASSPORTS = TypeAdapter(Passports)


def normalize_passports(value, *, legacy_string: bool = False, required: bool = False) -> list[str]:
    if legacy_string and isinstance(value, str):
        value = [value] if value else []
    result = _PASSPORTS.validate_python(value)
    if required and not result:
        raise ValueError("至少需要一个有效通行证")
    # 与中间件一致，不裁剪、不去重，保留业务值、顺序和重复项。
    return list(result)


def encode_passports(value: list[str]) -> str:
    return json.dumps(normalize_passports(value), ensure_ascii=False, separators=(",", ":"))


def decode_passports(value) -> list[str] | None:
    if value is None:
        return None
    return normalize_passports(json.loads(value) if isinstance(value, str) else value)


def passport_record(row) -> dict:
    result = dict(row)
    if "passport" in result:
        result["passport"] = decode_passports(result["passport"])
    return result


def same_passports(left: list[str] | None, right: list[str] | None) -> bool:
    """整份文件删除要求通行证全集一致；顺序和重复项不改变集合身份。"""
    return left is right if left is None or right is None else set(left) == set(right)
