"""用户文件访问等级与平台管理员关系的独立增量契约。"""
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from app.model.middleware_user import PlatformCode

Level=Annotated[int,Field(strict=True,ge=0)]


class UserAccessPatch(BaseModel):
    model_config=ConfigDict(frozen=True,extra='forbid')

    userid: str = Field(strict=True,min_length=1,max_length=128,description='需要更新的已登记员工 userid。')
    platform_permissions: dict[PlatformCode,Level | None] = Field(default_factory=dict,
        description='仅修改列出的平台；非负整数设置文件等级，null 移除平台授权，省略的平台保留。')
    platform_admins: dict[PlatformCode,StrictBool] = Field(default_factory=dict,
        description='仅修改列出的平台；true 添加管理员，false 移除管理员，不影响文件等级。')

    @field_validator('userid')
    @classmethod
    def validate_userid(cls,value):
        if not value.strip() or value != value.strip():raise ValueError('userid 不得为空或含首尾空白')
        return value

    @model_validator(mode='after')
    def require_change(self):
        if not self.platform_permissions and not self.platform_admins:
            raise ValueError('至少指定一项平台等级或管理员变更')
        return self


class UserAccessState(BaseModel):
    model_config=ConfigDict(frozen=True,extra='forbid')

    userid: str = Field(strict=True,min_length=1,max_length=128,description='员工 userid。')
    platform_permissions: dict[PlatformCode,Level] = Field(description='数据库当前完整文件等级映射。')
    managed_platforms: list[PlatformCode] = Field(description='数据库当前完整管理平台代码列表，按代码排序。')


def apply_access_patch(current: UserAccessState,patch: UserAccessPatch) -> UserAccessState:
    permissions=dict(current.platform_permissions)
    for code,level in patch.platform_permissions.items():
        if level is None:permissions.pop(code,None)
        else:permissions[code]=level
    admins=set(current.managed_platforms)
    for code,enabled in patch.platform_admins.items():
        if enabled:admins.add(code)
        else:admins.discard(code)
    return UserAccessState(userid=current.userid,platform_permissions=permissions,managed_platforms=sorted(admins))


class UserAccessError(RuntimeError):
    def __init__(self,message,*,status_code=503,phase='database',database_updated=False,pending_updates=None):
        super().__init__(message)
        self.status_code,self.phase=status_code,phase
        self.database_updated,self.pending_updates=database_updated,pending_updates
