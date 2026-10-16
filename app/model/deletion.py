"""无附件删除申请、消费确认与正式资源定位结果。"""

from datetime import datetime

from app.model.passport import Passports, normalize_passports
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator


class DeletionMessage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    message_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}$", strict=True)
    platform_code: str = Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", max_length=128, strict=True)
    source_id: str = Field(min_length=1, max_length=128, strict=True)
    passport: Passports = Field(min_length=1)
    name: str = Field(min_length=1, max_length=200, strict=True)
    abstract: str = Field(min_length=1, max_length=10000, strict=True)
    uploader: str = Field(min_length=1, max_length=200, strict=True)
    applicant: str = Field(min_length=1, max_length=200, strict=True)
    note: str = Field(default="", max_length=10000, strict=True)
    created_at: AwareDatetime

    @field_validator("source_id", "name", "abstract", "uploader", "applicant")
    @classmethod
    def reject_blank_text(cls, value):
        if not value.strip():
            raise ValueError("删除申请关键文本不能仅为空白")
        return value

    @field_validator("passport", mode="before")
    @classmethod
    def normalize_legacy_passport(cls, value):
        return normalize_passports(value, legacy_string=True, required=True)

    @field_validator("created_at", mode="before")
    @classmethod
    def parse_created_at(cls, value):
        if not isinstance(value, str):
            raise ValueError("删除申请时间必须为带时区的 ISO 8601 字符串")
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            raise ValueError("删除申请时间必须为带时区的 ISO 8601 字符串") from None


class DeletionDelivery(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    offset: int = Field(ge=0, le=18446744073709551615, strict=True)
    message: DeletionMessage


class DeletionAcknowledgement(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    message_id: str = Field(min_length=1, max_length=128, strict=True)
    status: Literal["acknowledged"]
    offset: int = Field(ge=0, strict=True)


class DeletionResource(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    resource_id: int = Field(gt=0, le=18446744073709551615)
    source_platform: str = Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", max_length=128)
    source_id: str = Field(min_length=1, max_length=128)
    file_url: str = Field(pattern=r"^[A-Za-z0-9_-]{1,128}\.[A-Za-z0-9]{1,16}$")
    passport: Passports | None = None
