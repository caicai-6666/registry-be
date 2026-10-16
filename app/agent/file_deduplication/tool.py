"""判重工具的模型可见契约与纯校验；不调用模型、不修改页面窗口。"""

from functools import lru_cache
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator

from app.agent.file_deduplication.state import FileDeduplicationResult


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, revalidate_instances="always")


class PageObservation(ToolArguments):
    current_page: int = Field(strict=True, ge=1, description="文件 A 的物理页码，从 1 开始；引用已经提供的 A 页面中的证据。")
    candidate_page: int = Field(strict=True, ge=1, description="文件 B 的物理页码，从 1 开始；只能引用已经展示的页面。")
    finding: str = Field(strict=True, min_length=1, max_length=1000,
                         description="这对页面中观察到的对应事实或关键差异，应简述可核对内容，不复制整页、不推测未见内容。")

    @field_validator("finding")
    @classmethod
    def nonblank_finding(cls, value):
        if not value.strip():
            raise ValueError("页面观察不能为空白")
        return value


class ReadCandidatePagesArguments(ToolArguments):
    """打开文件 B 的指定物理页，可附带当前可见页面的观察记录；文件 A 的全部页面已提供。"""

    page_numbers: list[Annotated[int, Field(strict=True, ge=1,
        description="文件 B 的一个有效物理页码，从 1 开始，不能超过 B 的总页数。")]] = Field(strict=True, min_length=1,
        json_schema_extra={"uniqueItems": True},
        description="要打开的文件 B 物理页码列表，从 1 开始，页码互不重复且不超过 B 的总页数；数量不超过同时可见的页面上限。")
    observations: list[PageObservation] = Field(default_factory=list, max_length=32,
        description="切换页面前需要保留的精简观察，可为空；B 证据必须来自当前可见窗口，A 证据来自常驻页面。校验或读页失败时不得提交这些观察。")

    @field_validator("page_numbers")
    @classmethod
    def strict_page_numbers(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("读取页码不能重复")
        return value


class FinishComparisonArguments(ToolArguments):
    """先提交文件 A 与 B 的页面证据和简洁论证，再给出是否重复及简明结论。"""

    evidence: list[PageObservation] = Field(min_length=1, max_length=32,
        description="支持最终判断的页面证据列表，至少一项；A 页码须有效，B 页码必须已经展示，可引用先前展示后折叠的页面证据。")
    reasoning: str = Field(strict=True, min_length=1, max_length=10000,
        description="根据上述页面证据给出支持最终判断的简洁论证，说明对应事实或实质差异，不输出探索过程；摘要只提供线索，不能替代页面证据。")
    is_duplicate: bool = Field(strict=True,
        description="基于 A 与 B 原文件证据的最终重复性判断，只接受 true 或 false；证据不足不能冒充 false。")
    conclusion: str = Field(strict=True, min_length=1, max_length=2000,
        description="面向阅读者的非空简明结论，说明两个文件重复或不同的关键依据；应与证据、论证及是否重复的判断一致，不重复展开完整论证。")

    @field_validator("reasoning", "conclusion")
    @classmethod
    def nonblank_text(cls, value):
        if not value.strip():
            raise ValueError("论证和最终结论不能为空白")
        return value


def _positive_count(value, name):
    if type(value) is not int or value < 1:
        raise ValueError(f"{name}必须为正整数")


@lru_cache(maxsize=32)
def _read_arguments_model(max_visible_pages: int):
    # 复用 Pydantic 字段定义，只增加配置决定的长度上限；Schema 与校验使用同一个模型。
    field = ReadCandidatePagesArguments.model_fields["page_numbers"]
    return create_model(
        "ReadCandidatePagesArguments",
        __base__=ReadCandidatePagesArguments,
        __doc__=ReadCandidatePagesArguments.__doc__,
        page_numbers=(field.annotation, Field(strict=True, min_length=1, max_length=max_visible_pages,
                      description=field.description, json_schema_extra={"uniqueItems": True})),
    )


def _function_tool(method, arguments_model):
    description = arguments_model.__doc__
    if not description or not description.splitlines()[0].strip():
        raise ValueError("工具参数模型必须在 docstring 第一行描述使用说明")
    return {"type": "function", "function": {
        "name": method.__name__, "description": description.splitlines()[0].strip(), "strict": False,
        "parameters": arguments_model.model_json_schema(),
    }}


def build_file_deduplication_tools(*, max_visible_pages: int) -> list[dict]:
    """按窗口配置构造两个稳定工具，不把 B 页数注入共享前缀。"""
    _positive_count(max_visible_pages, "视觉窗口上限")
    return [
        _function_tool(read_candidate_pages, _read_arguments_model(max_visible_pages)),
        _function_tool(finish_comparison, FinishComparisonArguments),
    ]


def _validate_observations(observations, *, current_page_count, candidate_page_count, allowed_candidate_pages):
    for observation in observations:
        if observation.current_page > current_page_count or observation.candidate_page > candidate_page_count:
            raise ValueError("证据页码超出文件页数")
        if observation.candidate_page not in allowed_candidate_pages:
            raise ValueError("证据引用的 B 页不在允许的已展示页面中")


def read_candidate_pages(arguments: ReadCandidatePagesArguments, *, current_page_count: int, candidate_page_count: int,
                                  max_visible_pages: int, visible_candidate_pages: set[int]) -> ReadCandidatePagesArguments:
    """校验读页参数及当前窗口引用，返回可供后续执行器接受的参数对象。"""
    for value, name in ((current_page_count, "A 页数"), (candidate_page_count, "B 页数"), (max_visible_pages, "视觉窗口上限")):
        _positive_count(value, name)
    parsed = _read_arguments_model(max_visible_pages).model_validate(arguments)
    if any(page > candidate_page_count for page in parsed.page_numbers):
        raise ValueError("读取页码越界或数量超过视觉窗口上限")
    _validate_observations(parsed.observations, current_page_count=current_page_count,
        candidate_page_count=candidate_page_count, allowed_candidate_pages=visible_candidate_pages)
    return parsed


def finish_comparison(arguments: FinishComparisonArguments, *, current_page_count: int, candidate_page_count: int,
                               presented_candidate_pages: set[int]) -> FinishComparisonArguments:
    """校验完成参数及已展示页引用，证据充分性和终态验收由运行器负责。"""
    _positive_count(current_page_count, "A 页数")
    _positive_count(candidate_page_count, "B 页数")
    parsed = FinishComparisonArguments.model_validate(arguments)
    _validate_observations(parsed.evidence, current_page_count=current_page_count,
        candidate_page_count=candidate_page_count, allowed_candidate_pages=presented_candidate_pages)
    return parsed


def comparison_result(arguments: FinishComparisonArguments) -> FileDeduplicationResult:
    """将已经接受的完成参数转换为仅包含 boolean 和理由的文件比较结果。"""
    parsed = FinishComparisonArguments.model_validate(arguments)
    return FileDeduplicationResult(is_duplicate=parsed.is_duplicate, reason=parsed.conclusion)
