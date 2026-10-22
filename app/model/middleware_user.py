"""中间件用户资料及登录查询的精确数据契约。"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator


PlatformCode = Annotated[str, Field(pattern=r'^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$')]


class MiddlewareUserKey(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)
    userid: str = Field(min_length=1, strict=True)

    @field_validator('userid')
    @classmethod
    def reject_blank(cls, value):
        if not value.strip():
            raise ValueError('用户标识不能全为空白')
        return value


class MiddlewareUserInfo(MiddlewareUserKey):
    username: str = Field(min_length=1, strict=True)
    permissions: dict[PlatformCode, StrictInt]

    @field_validator('username')
    @classmethod
    def reject_blank_name(cls, value):
        if not value.strip():
            raise ValueError('用户名不能全为空白')
        return value


class MiddlewareUserPatch(MiddlewareUserKey):
    username: str | None = Field(default=None, min_length=1, strict=True)
    permissions: dict[PlatformCode, StrictInt] | None = None

    @model_validator(mode='after')
    def validate_changes(self):
        provided = self.model_fields_set - {'userid'}
        if not provided or any(getattr(self, field) is None for field in provided):
            raise ValueError('至少更新一个资料字段，且不能提供 null')
        if self.username is not None and not self.username.strip():
            raise ValueError('用户名不能全为空白')
        return self


class MiddlewareUserLoginStatus(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    userid: str | None = Field(strict=True, min_length=1)
    username: str | None = Field(strict=True, min_length=1, description="中央当前用户名；离线时必须为 null。")
    is_logged_in: StrictBool
    ttl: int = Field(strict=True, ge=0)

    @model_validator(mode='after')
    def validate_session(self):
        # 离线必须明确返回 null，不能把旧 userid 误当成有效身份。
        if self.is_logged_in:
            if self.ttl <= 0 or self.userid is None or not self.userid.strip() or self.username is None or not self.username.strip():
                raise ValueError('有效登录必须包含用户标识、用户名及正有效期')
        elif self.ttl != 0 or self.userid is not None or self.username is not None:
            raise ValueError('离线登录必须返回 null 用户标识、用户名与零有效期')
        return self
