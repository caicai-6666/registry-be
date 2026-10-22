"""文件等级整数契约；等级含义独立于管理员身份，尚不限定业务枚举。"""

from typing import Annotated

from pydantic import Field, TypeAdapter

FileLevel = Annotated[int, Field(strict=True, ge=-(2**63), le=2**63-1)]
FILE_LEVEL = TypeAdapter(FileLevel)
