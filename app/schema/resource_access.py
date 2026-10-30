"""可信后台代用户读取正式原件的请求，不接受客户端权限或平台身份。"""

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ServiceFileRequest(BaseModel):
    model_config = ConfigDict(frozen=True, extra='forbid')

    userid: str = Field(strict=True, min_length=1, max_length=128,
        description='知识后端从可信任务上下文绑定的员工 userid；不得取自模型参数或未经验证的前端输入。')

    @field_validator('userid')
    @classmethod
    def reject_blank(cls, value: str) -> str:
        if not value.strip() or value != value.strip():
            raise ValueError('员工 userid 不得为空或含首尾空白')
        return value
