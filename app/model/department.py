"""人员管理使用的部门基本信息。"""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DepartmentInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int = Field(strict=True, ge=0, le=18446744073709551615, description="钉钉顶级部门 ID。")
    name: str = Field(strict=True, min_length=1, max_length=200, description="部门名称。")

    @field_validator("name")
    @classmethod
    def reject_blank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("部门名称不能全为空白")
        return value
