"""轻量删除审核的列表、历史、最终决定契约。"""

from typing import Literal

from app.model.file_level import FileLevel
from app.model.passport import Passports

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class DeletionReviewDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    approved: bool = Field(strict=True, description="是否批准删除正式文件。")
    note: str = Field(default="", strict=True, max_length=10000, description="审核意见，支持 Markdown；拒绝删除时必须提供非空理由。")

    @model_validator(mode="after")
    def validate_note(self):
        if not self.approved and not self.note.strip():
            raise ValueError("拒绝删除必须填写审核理由")
        return self


class DeletionReviewApplicationResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: int = Field(gt=0, description="SQLite 删除审核记录 ID。")
    resource_id: int | None = Field(gt=0, le=18446744073709551615, description="申请关联的正式资源 ID，自动拒绝未绑定资源时为 null。")
    source_platform: str = Field(description="申请来源平台代码。")
    source_id: str | None = Field(description="文件在来源平台中的 ID，历史未知时为 null。")
    passport: Passports | None = Field(description="申请删除时提交的原通行证数组，历史未知时为 null。")
    file_level: FileLevel | None = Field(description="删除申请关联正式文件的等级快照，历史未知或未绑定资源时为 null。")
    name: str | None = Field(description="申请时的业务文件名称快照，与磁盘文件名不同；历史未知时为 null。")
    abstract: str | None = Field(description="申请时的文件摘要快照；历史未知时为 null。")
    uploader: str | None = Field(description="申请中记录的原文件上传人，与删除申请人不同；历史未知时为 null。")
    applicant: str | None = Field(description="发起删除申请的人。")
    applicant_note: str = Field(description="删除申请人备注或删除原因。")
    requested_at: AwareDatetime = Field(description="删除申请时间，UTC。")


class PendingDeletionReviewResponse(DeletionReviewApplicationResponse):
    resource_id: int = Field(gt=0, le=18446744073709551615, description="待删除的正式资源 ID，可用于读取 resource 原件。")


class DeletionReviewHistoryResponse(DeletionReviewApplicationResponse):
    status: Literal["approved", "rejected"] = Field(description="本地已完成的删除审核状态。")
    reviewer: str = Field(description="注册中心最终审核人。")
    reviewer_note: str | None = Field(description="最终审核意见，历史未知时为 null。")
    reviewed_at: AwareDatetime | None = Field(description="审核时间，UTC；历史未知时为 null。")
    result_message_id: str | None = Field(description="中间件结果消息 ID，未发布或历史未知时为 null。")


class DeletionReviewResultResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    id: int = Field(gt=0, description="SQLite 删除审核记录 ID。")
    status: Literal["approved", "rejected"] = Field(description="最终删除审核状态。")
    approved: bool = Field(description="是否批准并完成本地删除。")
    resource_id: int = Field(gt=0, le=18446744073709551615, description="本次审核关联的原正式资源 ID；批准后该资源已移除。")
    reviewer: str = Field(description="从登录身份取得的注册中心审核人。")
    reviewer_note: str = Field(description="最终审核意见。")
    reviewed_at: AwareDatetime = Field(description="首次确定决定的 UTC 时间。")
    result_message_id: str = Field(description="中间件已确认发布的删除审核反馈消息 ID。")
