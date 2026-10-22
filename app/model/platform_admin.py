"""中间件当前平台成员查询与展示契约。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from app.model.middleware_user import PlatformCode

AdminUserID = Annotated[str, Field(strict=True, min_length=1, pattern=r'\S')]
ADMIN_USERIDS = TypeAdapter(list[AdminUserID])


class PlatformAdminProfile(BaseModel):
    """平台目录展示的管理员资料，不包含员工权限或登录凭据。"""
    model_config = ConfigDict(frozen=True, extra='forbid')

    userid: AdminUserID = Field(max_length=128, description='管理员的员工唯一 userid。')
    name: str = Field(strict=True, min_length=1, description='管理员姓名，历史未知时使用 userid。')
    avatar_url: str | None = Field(pattern=r'^[a-f0-9]{32}\.webp$',
        description='头像文件名，通过知识后端的头像接口读取；无头像时为 null。')


class PlatformAdminRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid', strict=True)

    platform_code: PlatformCode = Field(description='目标平台代码。')
    userid: AdminUserID = Field(max_length=128, description='管理员员工 userid。')


class PlatformAdminChange(PlatformAdminRequest):
    changed: bool
