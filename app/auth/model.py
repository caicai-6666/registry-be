"""认证后统一使用的服务器端员工身份。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AuthenticatedUser(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    userid: str = Field(min_length=1, max_length=128, strict=True, description="中间件验证用户 token 后返回的可信员工 userid。")
    name: str | None = Field(default=None, max_length=200, description="身份验证时为中央 username；员工仓库映射时为员工姓名。")
    department_id: int | None = Field(default=None, ge=0, description="员工所属的顶级部门 ID。")
    department_name: str | None = Field(default=None, max_length=200, description="顶级部门名称。")
    avatar_url: str | None = Field(default=None, pattern=r'^[a-f0-9]{32}\.webp$',
        description="头像文件名，不包含目录或接口路径；通过 resource 头像接口读取，未设置时为空。")
    platform_permissions: dict[str, Annotated[int, Field(strict=True, ge=0)]] = Field(
        default_factory=dict, description="以平台代码为键、文件访问权限等级为值的映射，不代表平台管理权限。"
    )
    managed_platforms: list[Annotated[str, Field(strict=True, pattern=r'^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$')]] = Field(
        default_factory=list, description="用户具有管理权限的平台代码列表，来自管理员关联；与文件访问等级独立。")

    @field_validator('managed_platforms')
    @classmethod
    def normalize_managed_platforms(cls, values: list[str]) -> list[str]:
        return sorted(set(values))


class RegistryAdminIdentity(BaseModel):
    """已通过中央登录及 registry 管理员校验的最小对外身份。"""
    model_config = ConfigDict(frozen=True, extra='forbid')

    userid: str = Field(strict=True, min_length=1, description='中央确认的当前用户唯一标识。')
    username: str = Field(strict=True, min_length=1, description='中央登录查询返回的当前用户名。')
