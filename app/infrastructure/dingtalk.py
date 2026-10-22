"""只读钉钉员工和部门资料，不承担用户登录。"""
import asyncio
import logging
import re
from time import monotonic

import httpx
from pydantic import BaseModel, Field, SecretStr

from app.config.dingtalk import DingTalkSettings
from app.auth.error import AuthUnavailable
from app.model.personnel_refresh import DingTalkEmployee, DingTalkDepartment


class _DingTalkLogFilter(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        if 'oapi.dingtalk.com' in message and 'access_token=' in message:
            # 旧通讯录 API 必须在 query 中携带应用 token，HTTP 客户端日志不能原样记录。
            record.msg = re.sub(r'access_token=[^&\s"\'<>]+', 'access_token=[redacted]', message)
            record.args = ()
        return True


logging.getLogger('httpx').addFilter(_DingTalkLogFilter())


class _ApplicationToken(BaseModel):
    accessToken: SecretStr = Field(min_length=1)
    expireIn: int = Field(strict=True,gt=0)


class DingTalkClient:
    def __init__(self,settings: DingTalkSettings,*,transport: httpx.AsyncBaseTransport | None=None):
        if not settings.configured:raise ValueError('钉钉应用凭据尚未配置')
        self._settings=settings
        self._http=httpx.AsyncClient(timeout=settings.timeout_seconds,trust_env=False,
                                    follow_redirects=False,transport=transport)
        self._token: SecretStr | None=None
        self._expires_at=0.0
        self._lock=asyncio.Lock()

    async def close(self):
        self._token=None
        await self._http.aclose()

    async def _application_token(self) -> str:
        async with self._lock:
            if self._token is None or monotonic() >= self._expires_at:
                try:
                    response=await self._http.post('https://api.dingtalk.com/v1.0/oauth2/accessToken',json={
                        'appKey':self._settings.app_key.get_secret_value(),
                        'appSecret':self._settings.app_secret.get_secret_value()})
                    response.raise_for_status()
                    if response.status_code != 200:raise ValueError('钉钉认证状态异常')
                    result=_ApplicationToken.model_validate(response.json())
                except (httpx.HTTPError,ValueError,TypeError):
                    raise AuthUnavailable('钉钉应用认证暂不可用') from None
                self._token=result.accessToken
                self._expires_at=monotonic()+result.expireIn-min(60,result.expireIn/10)
            return self._token.get_secret_value()

    async def _call(self,path: str,body: dict) -> dict:
        for attempt in range(2):
            token=await self._application_token()
            try:
                response=await self._http.post('https://oapi.dingtalk.com'+path,
                    params={'access_token':token},json=body)
                response.raise_for_status()
                payload=response.json()
                if response.status_code != 200 or not isinstance(payload,dict) or type(payload.get('errcode')) is not int:
                    raise ValueError('钉钉响应格式异常')
                code=payload['errcode']
                if code == 0:
                    if not isinstance(payload.get('result'),dict):raise ValueError('钉钉响应缺少结果')
                    return payload['result']
            except (httpx.HTTPError,ValueError,TypeError):
                raise AuthUnavailable('钉钉通讯录查询暂不可用') from None
            if code in (40014,42001,88) and attempt == 0:
                async with self._lock:
                    if self._token is not None and self._token.get_secret_value() == token:self._token=None
                continue
            raise AuthUnavailable('钉钉通讯录查询失败，请检查成员/部门读取权限及通讯录范围')
        raise AuthUnavailable('钉钉通讯录查询暂不可用')

    async def get_employee(self,userid: str) -> DingTalkEmployee:
        try:
            result=DingTalkEmployee.model_validate(await self._call('/topapi/v2/user/get',{'userid':userid,'language':'zh_CN'}))
            if result.userid != userid:raise ValueError('钉钉员工标识不一致')
            return result
        except (ValueError,TypeError):raise AuthUnavailable('钉钉员工资料响应异常') from None

    async def get_department(self,department_id: int) -> DingTalkDepartment:
        try:
            result=DingTalkDepartment.model_validate(await self._call('/topapi/v2/department/get',{'dept_id':department_id,'language':'zh_CN'}))
            if result.dept_id != department_id:raise ValueError('钉钉部门标识不一致')
            return result
        except (ValueError,TypeError):raise AuthUnavailable('钉钉部门资料响应异常') from None
