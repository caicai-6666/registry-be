"""中间件公开平台信息与本地内存快照，不包含认证凭据。"""

from dataclasses import dataclass
from datetime import datetime

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class PlatformInfo(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="ignore")

    code: str = Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", description="平台唯一代号。")
    name: str = Field(min_length=1, description="平台中文展示名称。")
    description: str = Field(min_length=1, description="平台用途说明。")
    supports_a2a: bool = Field(description="平台配置声明的是否参与 A2A 协议，不参与权限判断。")
    is_sub_management_platform: bool = Field(description="平台配置声明的是否为子管理平台，不参与权限判断。")
    is_management_entry: bool = Field(description="平台配置声明的是否作为管理平台入口，不参与权限判断。")
    a2aresponsibility: str = Field(description="平台在 A2A 协作中的职责，允许空字符串；仅保存于内部目录快照。")
    is_alive: bool = Field(description="中间件登录会话是否有效，不代表平台业务健康状态。")

    @field_validator("name", "description")
    @classmethod
    def reject_blank_text(cls, value):
        if not value.strip():
            raise ValueError("平台名称与描述不能仅包含空白")
        return value


class PlatformDirectoryEntry(PlatformInfo):
    """本地平台对象；上次存活时间由目录服务记录，不采信上游扩展字段。"""

    last_alive_at: AwareDatetime | None = Field(default=None,
        description="本进程最近一次同步确认该平台存活的 UTC 时间，没有可用存活记录时为 null。")


@dataclass(frozen=True)
class PlatformDirectorySnapshot:
    platforms: tuple[PlatformDirectoryEntry, ...] = ()
    updated_at: datetime | None = None
    is_stale: bool = True
    next_sync_at: datetime | None = None

    @property
    def alive_platforms(self) -> tuple[PlatformDirectoryEntry, ...]:
        """返回快照中存活的平台；调用者仍须检查快照是否过期。"""
        return tuple(platform for platform in self.platforms if platform.is_alive)

    def get(self, code: str) -> PlatformDirectoryEntry | None:
        return next((platform for platform in self.platforms if platform.code == code), None)
