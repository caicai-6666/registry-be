"""查询当前注册中心管理员身份；用户登录由知识后端负责。"""

from fastapi import APIRouter, Response

from app.auth.model import RegistryAdminIdentity
from app.router.dependency import PlatformAdminUserDependency

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get('/me', response_model=RegistryAdminIdentity, summary="查询当前注册中心管理员身份")
async def current_user(user: PlatformAdminUserDependency, response: Response):
    response.headers['Cache-Control'] = 'no-store'
    return RegistryAdminIdentity(userid=user.userid, username=user.name)
