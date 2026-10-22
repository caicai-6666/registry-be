"""正式文件等级调整请求、持久日志及安全错误边界。"""
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from app.model.file_level import FileLevel


class FileLevelAdjustmentRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    resource_id: int = Field(strict=True, gt=0, le=18446744073709551615, description="注册中心正式文件 ID。")
    file_level: FileLevel = Field(description="调整后的文件等级，有符号 64 位整数。")
    note: str = Field(default="", strict=True, max_length=10000, description="等级调整备注，允许为空。")


class FileLevelAdjustmentResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: int = Field(gt=0, description="SQLite 文件等级调整日志 ID。")
    resource_id: int = Field(gt=0, description="注册中心正式文件 ID。")
    previous_file_level: FileLevel | None = Field(description="调整前的文件等级，历史未知为 null。")
    file_level: FileLevel = Field(description="调整后的文件等级。")
    reviewer: str = Field(description="执行调整的管理员 userid。")
    note: str = Field(description="管理员填写的调整备注。")
    status: str = Field(pattern="^published$", description="published 表示本地更新及中间件发布均已确认。")
    result_message_id: str = Field(min_length=1, description="中间件确认的文件等级调整消息 ID。")
    created_at: AwareDatetime = Field(description="首次创建调整日志的 UTC 时间。")


class FileLevelAdjustmentHistoryResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: int = Field(gt=0, description="SQLite 文件等级调整日志 ID。")
    resource_id: int = Field(strict=True, gt=0, le=18446744073709551615, description="注册中心原正式文件 ID，文件删除后继续保留。")
    source_platform: str = Field(description="文件来源平台代码快照。")
    source_id: str = Field(description="文件在来源平台的 ID 快照。")
    previous_file_level: FileLevel | None = Field(description="调整前等级，历史未知为 null。")
    file_level: FileLevel = Field(description="目标文件等级；是否完成须结合 status 判断。")
    reviewer: str = Field(description="发起调整的管理员 userid。")
    reviewer_name: str = Field(description="发起调整时的管理员名称快照。")
    note: str = Field(description="等级调整备注。")
    status: Literal["prepared", "local_updated", "published"] = Field(
        description="prepared 表示已记录意图但本地提交未确认；local_updated 表示本地已提交；published 表示发布也已确认。")
    result_message_id: str | None = Field(description="已确认发布的中间件消息 ID，未确认时为 null。")
    created_at: AwareDatetime = Field(description="首次创建日志的 UTC 时间。")
    updated_at: AwareDatetime = Field(description="最近更新日志的 UTC 时间。")


class FileLevelAdjustmentError(ValueError):
    def __init__(self, message: str, *, status_code: int = 503, log_id: int | None = None,
                 database_updated: bool | None = False):
        super().__init__(message)
        self.status_code, self.log_id, self.database_updated = status_code, log_id, database_updated
