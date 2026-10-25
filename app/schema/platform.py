"""平台查看接口的公开响应，不暴露本地存活观测记录或认证信息。"""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.model.platform_admin import PlatformAdminProfile


class PlatformResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(description="平台唯一代码。")
    name: str = Field(description="平台名称。")
    description: str = Field(description="平台用途描述。")
    supports_a2a: bool = Field(description="平台配置声明的是否参与 A2A 协议，不参与权限判断。")
    is_sub_management_platform: bool = Field(description="平台配置声明的是否为子管理平台，不参与权限判断。")
    is_management_entry: bool = Field(description="平台配置声明的是否作为管理平台入口，不参与权限判断。")
    is_alive: bool = Field(description="最近一次平台同步确认的中间件会话存活状态。")
    admins: list[PlatformAdminProfile] = Field(description="平台管理员资料列表，每项为 userid、名称及头像文件名；无管理员时为空数组。")


class PlatformSyncTimeResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    next_sync_at: AwareDatetime | None = Field(description="后台下一次计划同步或重试的 UTC 时间；正在同步、未启用或已停止且没有计划时为 null。")
