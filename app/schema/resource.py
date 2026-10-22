"""正式文件基本信息接口的公开响应。"""

from app.model.passport import Passports
from app.model.file_level import FileLevel

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ResourceBasicInfoResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: int = Field(gt=0, le=18446744073709551615, strict=True, description="文件在注册中心的正式资源 ID。")
    source_platform: str = Field(description="文件来源平台代码。")
    name: str | None = Field(description="入库申请中的文件名称，即 resources.name；历史未知时为 null。")
    applicant: str | None = Field(description="文件入库申请人，历史未知时为 null。")
    reviewer: str | None = Field(description="批准文件入库的注册中心审核人，历史未知时为 null。")
    ingested_at: AwareDatetime = Field(description="正式资源记录创建时间，即 resources.created_at，使用 UTC。")
    abstract: str | None = Field(description="文件摘要，历史未知时为 null。")
    passport: Passports | None = Field(description="文件正式入库的最终通行证数组，即 resources.passport；历史未签发时为 null。")

    file_level: FileLevel | None = Field(default=None, description="文件当前等级，初次入库确定后可由管理员调整；历史未知时为 null。")
