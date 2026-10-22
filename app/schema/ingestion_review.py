"""人工入库审核的公开请求与响应，不向前端暴露摘要向量。"""

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from app.agent.passport_recommendation.state import PassportRecommendation
from app.model.passport import Passports
from app.model.file_level import FileLevel


class IngestionReviewDecision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    approved: bool = Field(strict=True, description="是否同意文件正式入库。")
    file_level: FileLevel | None = Field(default=None, description="批准入库时必填的文件等级，严格的有符号 64 位整数；拒绝时不设置。")
    note: str = Field(default="", strict=True, max_length=10000, description="审核人意见，支持 Markdown；拒绝时必须提供非空理由。")
    passport: Passports | None = Field(default=None,
        description="通过时可选的通行证数组，每项最多 512 字符且非空白；未提供则生成单元素数组。拒绝时只能省略或传空数组。")

    @model_validator(mode="after")
    def validate_decision(self):
        if self.approved and self.file_level is None:
            raise ValueError("同意入库必须提供文件等级 file_level")
        if not self.approved and self.file_level is not None:
            raise ValueError("拒绝入库不能设置文件等级")
        if self.approved and self.passport is not None and not self.passport:
            raise ValueError("同意入库时通行证数组不能为空")
        if not self.approved:
            if not self.note.strip():
                raise ValueError("拒绝入库必须填写审核理由")
            if self.passport not in (None, []):
                raise ValueError("拒绝入库不能签发通行证")
        return self


class PendingIngestionReviewResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int = Field(gt=0, description="SQLite 入库审核记录 ID，用于审核提交与待审原件读取。")
    source_platform: str = Field(description="申请来源平台代码。")
    source_id: str | None = Field(description="文件在来源平台中的 ID，历史记录可能为空。")
    name: str | None = Field(description="入库申请中的文件名称。")
    abstract: str | None = Field(description="文件摘要。")
    applicant: str | None = Field(description="来源平台发起入库申请的人，当前上游消息 reviewer 映射到此字段。")
    applicant_note: str = Field(description="入库申请人备注。")
    requested_at: AwareDatetime = Field(description="入库申请时间，UTC。")
    passport_recommendations: list[PassportRecommendation] | None = Field(
        description="通行证及推荐理由；null 表示历史记录未推荐，空数组表示无推荐。")


class IngestionReviewResultResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int = Field(gt=0, description="SQLite 入库审核记录 ID。")
    status: Literal["approved", "rejected"] = Field(description="已完成的最终入库审核状态。")
    resource_id: int | None = Field(description="通过后关联的 MySQL 正式资源 ID，拒绝时为 null。")
    passport: Passports = Field(description="通过时的通行证数组；拒绝时为空数组。")
    file_level: FileLevel | None = Field(description="批准时确定的文件等级，拒绝或历史未知时为 null。")
    reviewer: str = Field(description="从登录身份取得的审核人 userid。")
    reviewer_note: str = Field(description="最终审核意见。")
    reviewed_at: AwareDatetime = Field(description="审核决定确定的 UTC 时间。")
    result_message_id: str = Field(description="中间件已确认发布的审核结果消息 ID。")


class IngestionReviewHistoryResponse(PendingIngestionReviewResponse):
    """历史保留申请快照与最终结论，兼容旧记录缺少通行证、审核时间或发布回执。"""

    id: int = Field(gt=0, description="SQLite 入库审核记录 ID，与正式资源 resource_id 含义不同。")
    resource_id: int | None = Field(gt=0, le=18446744073709551615, description="关联的 MySQL 正式资源 ID，未创建资源时为 null。")
    status: Literal["approved", "rejected"] = Field(description="本地已完成的审核结论：通过或拒绝。")
    passport: Passports | None = Field(description="最终通行证数组，拒绝为空数组，历史未知时为 null。")
    file_level: FileLevel | None = Field(description="入库审核时确定的文件等级，拒绝或历史未知时为 null。")
    reviewer: str = Field(description="作出最终决定的审核人标识，新记录为 userid，历史可能为姓名。")
    reviewer_note: str | None = Field(description="最终审核意见，历史未知时为 null。")
    reviewed_at: AwareDatetime | None = Field(description="审核时间，UTC；历史未知时为 null。")
    result_message_id: str | None = Field(description="中间件已发布的反馈消息 ID；未发布或历史未知时为 null。")
