"""入库申请消息、正式资源与文件存储结果。"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, NotRequired, TypedDict

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator


class ResourceCandidate(TypedDict):
    resource_id: int
    source_platform: str
    source_id: str | None
    file_url: str
    name: str | None
    abstract: str | None
    similarity: float
    applicant: NotRequired[str | None]
    reviewer: NotRequired[str | None]
    passport: NotRequired[list[str] | None]


class IngestionFileReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    file_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$", strict=True)
    file_extension: str = Field(pattern=r"^[A-Za-z0-9]{1,16}$", strict=True)

    @property
    def filename(self) -> str:
        return f"{self.file_id}.{self.file_extension.lower()}"


class IngestionMessage(BaseModel):
    # 兼容扩展消息字段，只接收当前业务契约中的信息。
    model_config = ConfigDict(frozen=True, extra="ignore")

    message_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$", strict=True)
    platform_code: str = Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", max_length=128, strict=True)
    source_id: str | None = Field(default=None, min_length=1, max_length=128, strict=True)
    name: str | None = Field(default=None, min_length=1, max_length=200, strict=True)
    abstract: str | None = Field(default=None, min_length=1, max_length=10000, strict=True)
    note: str = Field(default="", max_length=10000, strict=True)
    reviewer: str = Field(min_length=1, max_length=200, strict=True)
    applicant: str | None = Field(default=None, min_length=1, max_length=200, strict=True,
        description="注册中心入库申请人；兼容历史显式值，当前消息未提供时使用上游 reviewer 发起人。")
    file: IngestionFileReference
    created_at: AwareDatetime

    @field_validator("created_at", mode="before")
    @classmethod
    def parse_created_at(cls, value):
        if not isinstance(value, str):
            raise ValueError("申请时间必须为带时区的 ISO 8601 字符串")
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("申请时间必须为带时区的 ISO 8601 字符串") from None

    @model_validator(mode="after")
    def validate_identity(self):
        if self.file.file_id != self.message_id:
            raise ValueError("附件标识必须与入库消息标识一致")
        for value in (self.source_id, self.name, self.abstract, self.reviewer, self.applicant):
            if value is not None and not value.strip():
                raise ValueError("申请关键文本不得仅为空白")
        # 当前上游 reviewer 是发起入库的人，在注册中心映射为申请人；兼容旧消息显式提供 applicant。
        if self.applicant is None:
            object.__setattr__(self, "applicant", self.reviewer)
        return self

    @property
    def file_url(self) -> str:
        return self.file.filename

    @property
    def relative_file_path(self) -> str:
        return f"{self.platform_code}/{self.file.filename}"


class IngestionDelivery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    offset: int = Field(ge=0, le=18446744073709551615, strict=True)
    message: IngestionMessage


class IngestionAcknowledgement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    message_id: str = Field(min_length=1, max_length=128, strict=True)
    status: Literal["acknowledged"]
    offset: int = Field(ge=0, strict=True)


@dataclass(frozen=True)
class StoredFile:
    file_url: str


@dataclass(frozen=True)
class StoredResource:
    message_id: str
    source_platform: str
    source_id: str | None
    file: StoredFile
    resource_id: int | None = None
    file_level: int | None = None


class ActiveSourceConflict(ValueError):
    """同一平台的源文件已有待审核申请或正式资源。"""

    def __init__(self, resource: StoredResource):
        self.resource = resource
        super().__init__("同平台源文件已存在，不能再次申请入库")
