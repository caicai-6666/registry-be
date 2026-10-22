"""员工列表的单表数据契约，不包含部门或管理员关联信息。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from app.model.middleware_user import PlatformCode


class EmployeeInfo(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    userid: str = Field(strict=True, min_length=1, max_length=128, description="员工唯一 userid。")
    name: str | None = Field(default=None, strict=True, min_length=1, max_length=200,
                             description="员工姓名，未补全时为 null。")
    avatar_url: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}\.webp$",
                                   description="头像文件名，无头像时为 null。")
    department_id: int = Field(strict=True, ge=0, le=18446744073709551615,
                               description="所属顶级部门 ID，由前端关联部门列表。")
    platform_permissions: dict[PlatformCode, Annotated[int, Field(strict=True, ge=0)]] = Field(
        description="平台文件访问等级映射；缺少平台键表示未授权，不代表管理权限。")
