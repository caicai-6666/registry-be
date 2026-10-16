"""子图只接收一对文件，只输出已校验的判重结论和理由。"""

from typing import TypedDict

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator

from app.model.pdf import PreparedPDF


class ComparisonFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str | None = Field(max_length=200, description="文件展示名称，未知时为 null。")
    abstract: str | None = Field(max_length=10000, description="文件摘要，未知时为 null。")
    pdf: PreparedPDF = Field(description="包含全部内存 PNG 页面及页面 UUID 的 PDF 渲染对象。")
    applicant: str | None = Field(default=None, max_length=200, description="文件元数据中 applicant 对应的人员名称，未知为 null；不能作为内容相同的证明。")
    reviewer: str | None = Field(default=None, max_length=200, description="文件元数据中 reviewer 对应的人员名称，未知为 null；不能作为内容相同的证明。")

    @model_validator(mode="after")
    def validate_pages(self):
        if self.pdf.page_count <= 0 or len(self.pdf.pages) != self.pdf.page_count:
            raise ValueError("比较文件必须包含全部渲染页面")
        if [page.page_number for page in self.pdf.pages] != list(range(1, self.pdf.page_count + 1)):
            raise ValueError("比较文件页码必须连续且从 1 开始")
        if any(not page.png_bytes for page in self.pdf.pages):
            raise ValueError("比较文件的页面 PNG 不能为空")
        return self


class FileDeduplicationInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    current_file: ComparisonFile = Field(description="文件 A 的名称、摘要和全部 PDF 页面。")
    similar_file: ComparisonFile = Field(description="文件 B 的名称、摘要和全部 PDF 页面，用于与文件 A 比较。")


class FileDeduplicationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    is_duplicate: StrictBool = Field(description="智能体明确判断两份文件是否重复，不接受未知或字符串布尔值。")
    reason: str = Field(strict=True, min_length=1, max_length=10000, description="两个文件重复或不同的最终简明结论。")

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value):
        if not value.strip():
            raise ValueError("判重理由不能为空白")
        return value


class FileDeduplicationInputState(TypedDict):
    pair: FileDeduplicationInput


class FileDeduplicationOutputState(TypedDict):
    result: FileDeduplicationResult


class FileDeduplicationState(TypedDict, total=False):
    pair: FileDeduplicationInput
    result: FileDeduplicationResult
