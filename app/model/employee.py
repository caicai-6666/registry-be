"""员工列表的单表数据契约，不包含部门或管理员关联信息。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.model.middleware_user import PlatformCode


class EmployeeWorkProfile(BaseModel):
    """与知识后端共用的任职资料契约，不进入登录身份或中央姓名同步。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    manager_userid: str | None = Field(default=None, strict=True, max_length=128,
        description="钉钉直属主管的企业 userid，未维护时为空，不以部门负责人替代。")
    title: str | None = Field(default=None, strict=True, max_length=200,
        description="钉钉维护的员工职位名称，未维护时为空。")

    @field_validator("manager_userid", "title")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None


class EmployeeInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    userid: str = Field(strict=True, min_length=1, max_length=128, description="员工唯一 userid。")
    name: str | None = Field(default=None, strict=True, min_length=1, max_length=200,
                             description="员工姓名，未补全时为 null。")
    avatar_url: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}\.webp$",
                                   description="头像文件名，无头像时为 null。")
    department_id: int = Field(strict=True, ge=0, le=18446744073709551615,
                               description="旧版兼容部门 ID，完整归属使用 department_ids，不代表主部门。")
    department_ids: list[Annotated[int, Field(strict=True, ge=0)]] = Field(min_length=1, description='全部一级部门ID，关联部门列表取得名称；无主次之分。')
    platform_permissions: dict[PlatformCode, Annotated[int, Field(strict=True, ge=0)]] = Field(
        description="平台文件访问等级映射；缺少平台键表示未授权，不代表管理权限。")
