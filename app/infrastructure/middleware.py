"""middleware-service 认证、平台目录、入库与删除接口的异步 HTTP 客户端。"""

from typing import Literal
from contextlib import asynccontextmanager

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, TypeAdapter, model_validator

from app.model.resource import IngestionAcknowledgement, IngestionDelivery, IngestionFileReference
from app.model.platform import PlatformInfo
from app.model.passport import normalize_passports
from app.model.deletion import DeletionAcknowledgement, DeletionDelivery

_PLATFORM_LIST = TypeAdapter(list[PlatformInfo])


class MiddlewareSessionResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    code: str = Field(pattern=r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
    expires_in_seconds: int = Field(gt=0, strict=True)
    heartbeat_interval_seconds: int = Field(gt=0, strict=True)

    @model_validator(mode="after")
    def validate_interval(self):
        if self.heartbeat_interval_seconds >= self.expires_in_seconds:
            raise ValueError("心跳间隔必须小于会话有效期")
        return self


class MiddlewareLoginResponse(MiddlewareSessionResponse):
    access_token: SecretStr = Field(min_length=1)
    token_type: Literal["bearer"]

    @model_validator(mode="after")
    def validate_token(self):
        token = self.access_token.get_secret_value()
        if token != token.strip() or not token:
            raise ValueError("访问令牌不得为空白或含首尾空白")
        return self


class IngestionResultPublished(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")
    message_id: str = Field(min_length=1, max_length=128, strict=True)
    status: Literal["published"]


class MiddlewareClient:
    def __init__(
        self, base_url: str, *, timeout_seconds: float = 40,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/") + "/",
            timeout=timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    @staticmethod
    def _validate_response(response: httpx.Response, model, platform_code: str):
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("中间件成功响应状态必须为 200")
        result = model.model_validate(response.json())
        if result.code != platform_code:
            raise ValueError("中间件响应平台与请求不一致")
        return result

    async def login(self, platform_code: str, secret: SecretStr) -> MiddlewareLoginResponse:
        response = await self._http.post(
            "middleware-service/api/platform/login",
            json={"platform_code": platform_code, "secret": secret.get_secret_value()},
        )
        return self._validate_response(response, MiddlewareLoginResponse, platform_code)

    async def heartbeat(self, platform_code: str, token: SecretStr) -> MiddlewareSessionResponse:
        # 当前契约仅使用令牌识别平台，不发送 JSON 身份或其他请求体。
        response = await self._http.post(
            "middleware-service/api/platform/heartbeat",
            headers={"Authorization": f"Bearer {token.get_secret_value()}"},
        )
        return self._validate_response(response, MiddlewareSessionResponse, platform_code)

    async def pull_ingestion(self, token: SecretStr) -> IngestionDelivery | None:
        response = await self._http.post(
            "middleware-service/api/ingestion-requests/pull",
            headers={"Authorization": f"Bearer {token.get_secret_value()}"},
        )
        response.raise_for_status()
        if response.status_code == 204:
            return None
        if response.status_code != 200:
            raise ValueError("入库拉取必须返回 200 或 204")
        return IngestionDelivery.model_validate(response.json())

    async def list_platforms(self, token: SecretStr) -> tuple[PlatformInfo, ...]:
        response = await self._http.get(
            "middleware-service/api/platform/list",
            headers={"Authorization": f"Bearer {token.get_secret_value()}"},
        )
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("平台列表必须返回 200")
        platforms = _PLATFORM_LIST.validate_python(response.json(), strict=True)
        if len({platform.code for platform in platforms}) != len(platforms):
            raise ValueError("平台列表中存在重复代号")
        return tuple(platforms)

    @asynccontextmanager
    async def open_ingestion_file(self, reference: IngestionFileReference, token: SecretStr):
        # 下载地址仅由已校验的文件引用构建，不向消息中的任意 URL 发送认证令牌。
        async with self._http.stream(
            "GET", f"middleware-service/api/resource/ingestion-requests/{reference.file_id}",
            params={"file_extension": reference.file_extension.lower()},
            headers={"Authorization": f"Bearer {token.get_secret_value()}"},
        ) as response:
            response.raise_for_status()
            if response.status_code != 200:
                raise ValueError("附件下载必须返回 200")
            if response.headers.get("content-encoding", "identity") != "identity":
                raise ValueError("审核附件必须按原始字节传输")
            yield response

    async def acknowledge_ingestion(self, delivery: IngestionDelivery, token: SecretStr) -> None:
        response = await self._http.post(
            "middleware-service/api/ingestion-requests/ack",
            headers={"Authorization": f"Bearer {token.get_secret_value()}"},
            json={"message_id": delivery.message.message_id},
        )
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("入库确认必须返回 200")
        result = IngestionAcknowledgement.model_validate(response.json())
        if result.message_id != delivery.message.message_id or result.offset != delivery.offset:
            raise ValueError("确认响应与当前入库消息不一致")

    async def publish_ingestion_rejection(
        self, delivery: IngestionDelivery, token: SecretStr, *, note: str,
    ) -> str:
        if not isinstance(note, str) or not note.strip() or len(note) > 10000:
            raise ValueError("拒绝审核意见不能为空且最多 10000 字符")
        return await self.publish_ingestion_result(
            request_id=delivery.message.message_id, target_platform_code=delivery.message.platform_code,
            token=token, note=note, reviewer="注册中心自动审核员", passport=[],
        )

    async def publish_ingestion_result(
        self, *, request_id: str, target_platform_code: str, token: SecretStr,
        note: str, reviewer: str, passport: list[str],
    ) -> str:
        """人工与自动审核共用反馈契约：非空数组表示通过，空数组表示拒绝。"""
        for value, maximum in ((request_id, 128), (target_platform_code, 128), (reviewer, 200)):
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ValueError("审核反馈身份字段不合法")
        if not isinstance(note, str) or len(note) > 10000:
            raise ValueError("审核反馈意见或通行证不合法")
        passport = normalize_passports(passport)
        response = await self._http.post(
            "middleware-service/api/ingestion-results/publish",
            headers={"Authorization": f"Bearer {token.get_secret_value()}"},
            json={"request_id": request_id, "target_platform_code": target_platform_code,
                  "note": note, "reviewer": reviewer, "passport": passport},
        )
        response.raise_for_status()
        if response.status_code != 201:
            raise ValueError("审核结果发布必须返回 201")
        return IngestionResultPublished.model_validate(response.json()).message_id

    async def close(self) -> None:
        await self._http.aclose()

    async def pull_deletion(self, token: SecretStr) -> DeletionDelivery | None:
        response = await self._http.post(
            "middleware-service/api/deletion-requests/pull",
            headers={"Authorization": f"Bearer {token.get_secret_value()}"},
        )
        response.raise_for_status()
        if response.status_code == 204:
            return None
        if response.status_code != 200:
            raise ValueError("删除拉取必须返回 200 或 204")
        return DeletionDelivery.model_validate(response.json())

    async def acknowledge_deletion(self, delivery: DeletionDelivery, token: SecretStr) -> None:
        response = await self._http.post(
            "middleware-service/api/deletion-requests/ack",
            headers={"Authorization": f"Bearer {token.get_secret_value()}"},
            json={"message_id": delivery.message.message_id},
        )
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError("删除确认必须返回 200")
        result = DeletionAcknowledgement.model_validate(response.json())
        if result.message_id != delivery.message.message_id or result.offset != delivery.offset:
            raise ValueError("确认响应与当前删除申请不一致")

    async def publish_deletion_rejection(self, delivery: DeletionDelivery, token: SecretStr, *, note: str) -> str:
        if not isinstance(note, str) or not note.strip() or len(note) > 10000:
            raise ValueError("删除拒绝意见不能为空且最多 10000 字符")
        return await self.publish_deletion_result(request_id=delivery.message.message_id,
            target_platform_code=delivery.message.platform_code, token=token,
            approved=False, note=note, reviewer="注册中心自动审核员")

    async def publish_deletion_result(self, *, request_id: str, target_platform_code: str, token: SecretStr,
                                      approved: bool, note: str, reviewer: str) -> str:
        for value, maximum in ((request_id, 128), (target_platform_code, 128), (reviewer, 200)):
            if not isinstance(value, str) or not value.strip() or len(value) > maximum:
                raise ValueError("删除反馈身份字段不合法")
        if type(approved) is not bool or not isinstance(note, str) or len(note) > 10000:
            raise ValueError("删除反馈决定或意见不合法")
        response = await self._http.post(
            "middleware-service/api/deletion-results/publish",
            headers={"Authorization": f"Bearer {token.get_secret_value()}"},
            json={"request_id": request_id, "target_platform_code": target_platform_code,
                  "approved": approved, "note": note, "reviewer": reviewer},
        )
        response.raise_for_status()
        if response.status_code != 201:
            raise ValueError("删除结果发布必须返回 201")
        return IngestionResultPublished.model_validate(response.json()).message_id
