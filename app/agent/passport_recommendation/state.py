"""名称、摘要与摘要向量输入，以及保留推荐理由的通行证列表。"""

from typing import Annotated, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field, StrictBool, ValidationInfo, field_validator, model_validator
from app.model.pdf import PreparedPDF

VectorValue = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class PassportRecommendationInput(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    name: str | None = Field(strict=True, max_length=200, description="当前文件展示名称，未提供时为 null。")
    abstract: str = Field(strict=True, min_length=1, max_length=10000, description="当前文件摘要，不得为空白。")
    abstract_embedding: list[VectorValue] = Field(strict=True, min_length=1, max_length=4096, description="当前文件摘要的非零有限向量，复用前置向量化结果。")

    @field_validator("name", "abstract")
    @classmethod
    def reject_blank_text(cls, value):
        if value is not None and not value.strip():
            raise ValueError("文件名称和摘要不能仅为空白")
        return value

    @field_validator("abstract_embedding")
    @classmethod
    def reject_zero_vector(cls, value):
        if not any(value):
            raise ValueError("摘要向量不能为零向量")
        return value


class PassportRecommendation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    passport: str = Field(strict=True, min_length=1, max_length=512, description="建议审核者选择的已有通行证字符串。")
    reason: str = Field(strict=True, min_length=1, max_length=10000, description="智能体给出该通行证建议的理由。")

    @field_validator("passport", "reason")
    @classmethod
    def reject_blank_value(cls, value):
        if not value.strip():
            raise ValueError("通行证和推荐理由不能仅为空白")
        return value


class PassportRecommendationResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    recommendations: list[PassportRecommendation] = Field(strict=True, description="按推荐顺序排列的通行证及理由列表；明确无候选时为空数组。")

    @field_validator("recommendations")
    @classmethod
    def reject_duplicate_passports(cls, value):
        if len({item.passport for item in value}) != len(value):
            raise ValueError("推荐列表不能包含重复通行证")
        return value


class PassportGroupFile(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    resource_id: int = Field(strict=True, gt=0, description="正式文件的注册中心唯一 ID，用于识别组内文件。")
    source_platform: str = Field(strict=True, min_length=1, max_length=128, description="文件来源平台，可与其他组内文件不同。")
    name: str | None = Field(strict=True, max_length=200, description="文件展示名称，未知为 null。")
    abstract: str | None = Field(strict=True, max_length=10000, description="文件摘要，未知为 null，不补造内容。")
    similarity: float = Field(strict=True, ge=-1, le=1, allow_inf_nan=False, description="文件摘要与当前文件摘要的余弦相似度。")

    @field_validator("source_platform")
    @classmethod
    def nonblank_platform(cls, value):
        if not value.strip():
            raise ValueError("来源平台不能为空白")
        return value


class PassportGroup(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    passport: str = Field(strict=True, min_length=1, max_length=512, description="分组绑定的已有通行证，组内文件共享此字符串。")
    files: list[PassportGroupFile] = Field(strict=True, min_length=1, description="符合检索条件的该通行证正式文件，允许来自多个平台。")

    @field_validator("passport")
    @classmethod
    def nonblank_passport(cls, value):
        if not value.strip():
            raise ValueError("通行证不能为空白")
        return value

    @field_validator("files")
    @classmethod
    def unique_files(cls, value):
        if len({item.resource_id for item in value}) != len(value):
            raise ValueError("通行证组不能包含重复文件")
        return value


class PassportRecommendationEvidence(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    current_page: int = Field(strict=True, gt=0, description="当前文件已展示的物理页码，从 1 开始，不是页面印刷编号。")
    group_file_index: int = Field(strict=True, gt=0, description="本组已展示文件条目的序号，从 1 开始；对应条目标签，不是该文件的页码。")
    finding: str = Field(strict=True, min_length=1, max_length=2000, description="可核对的当前页事实及对应条目名称或摘要中的关联、差异或限制；不得虚构该条目原文。")

    @field_validator("finding")
    @classmethod
    def nonblank_finding(cls, value):
        if not value.strip():
            raise ValueError("证据说明不能为空白")
        return value


class PassportGroupJudgment(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", revalidate_instances="always")

    evidence: list[PassportRecommendationEvidence] = Field(strict=True, max_length=32, description="依据当前文件页面及本组条目名称、摘要形成的证据；推荐时至少一条，不推荐且无可核对关联时可为空。")
    reasoning: str = Field(strict=True, min_length=1, max_length=10000, description="说明证据如何支持推荐决定，并交代冲突或不足；只给简洁论证摘要，不输出完整思考过程。")
    should_recommend: StrictBool = Field(description="是否建议当前文件关联本组已有通行证；依据不足时不推荐，不等于证明文件互不相关。")
    reason: str = Field(strict=True, min_length=1, max_length=10000, description="面向审核者、可独立阅读的简明推荐或不推荐理由，不含实现过程或新的无依据事实。")

    @field_validator("reasoning", "reason")
    @classmethod
    def nonblank_reason(cls, value):
        if not value.strip():
            raise ValueError("判断理由不能为空白")
        return value

    @model_validator(mode="after")
    def validate_evidence(self, info: ValidationInfo):
        if self.should_recommend and not self.evidence:
            raise ValueError("推荐必须具有可核对证据")
        bounds = info.context or {}
        for item in self.evidence:
            if "current_page_count" in bounds and item.current_page > bounds["current_page_count"]:
                raise ValueError("证据引用了未展示的当前文件页面")
            if "group_file_count" in bounds and item.group_file_index > bounds["group_file_count"]:
                raise ValueError("证据引用了本组不存在的文件条目")
        return self


class PassportGroupDecision(PassportGroupJudgment):
    passport: str = Field(strict=True, min_length=1, max_length=512, description="程序绑定的已有通行证原字符串，不由模型生成。")

    @field_validator("passport")
    @classmethod
    def nonblank_passport(cls, value):
        if not value.strip():
            raise ValueError("通行证不能为空白")
        return value


class PassportRecommendationInputState(TypedDict):
    file: PassportRecommendationInput
    current_pdf: NotRequired[PreparedPDF | None]


class PassportRecommendationOutputState(TypedDict):
    recommendations: list[PassportRecommendation]


class PassportRecommendationState(TypedDict, total=False):
    file: PassportRecommendationInput
    current_pdf: PreparedPDF | None
    groups: list[PassportGroup]
    decisions: list[PassportGroupDecision]
    recommendations: list[PassportRecommendation]
