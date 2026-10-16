"""平台查看接口的公开响应，不暴露本地存活观测记录或认证信息。"""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class PlatformResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str = Field(description="平台唯一代码。")
    name: str = Field(description="平台名称。")
    description: str = Field(description="平台用途描述。")
    is_alive: bool = Field(description="最近一次平台同步确认的中间件会话存活状态。")


class PlatformSyncTimeResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    next_sync_at: AwareDatetime | None = Field(description="后台下一次计划同步或重试的 UTC 时间；正在同步、未启用或已停止且没有计划时为 null。")
