"""读取后台平台目录快照，查看全部平台及存活状态、下次计划同步时间。"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.auth.error import AuthUnavailable
from app.router.dependency import PlatformAdminServiceDependency
from app.schema.platform import PlatformResponse, PlatformSyncTimeResponse
from app.service.platform_directory import PlatformDirectoryService

router = APIRouter(prefix="/platform", tags=["platform"],
    responses={401: {"description": "access-token 缺失、无效或已过期"},
               503: {"description": "平台目录服务不可用；列表接口也在快照过期时返回此状态"}})


async def get_platform_directory_service(request: Request) -> PlatformDirectoryService:
    service = getattr(request.app.state, "platform_directory_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="平台目录服务不可用", headers={"Cache-Control": "no-store"})
    return service


PlatformDirectoryDependency = Annotated[PlatformDirectoryService, Depends(get_platform_directory_service)]


@router.get("/list", response_model=list[PlatformResponse], summary="查看全部平台及存活状态")
async def list_platforms(response: Response, service: PlatformDirectoryDependency,
                         admins: PlatformAdminServiceDependency) -> list[PlatformResponse]:
    snapshot = service.get_snapshot()
    if snapshot.is_stale:
        # 上一次存活不等于当前存活；未知状态不能伪装成空列表或仍在线。
        raise HTTPException(status_code=503, detail="平台目录尚未同步或已过期，请稍后重试",
                            headers={"Cache-Control": "no-store"})
    response.headers["Cache-Control"] = "no-store"
    platforms = snapshot.platforms
    if not platforms:
        return []
    try:
        profiles = await admins.list_admin_profiles()
    except AuthUnavailable as error:
        raise HTTPException(status_code=503, detail=str(error),
                            headers={"Cache-Control": "no-store"}) from None
    return [PlatformResponse(code=platform.code, name=platform.name, description=platform.description,
                             is_alive=platform.is_alive, admins=profiles.get(platform.code, []))
            for platform in platforms]


@router.get("/sync-time", response_model=PlatformSyncTimeResponse,
            summary="查看下次平台同步时间", responses={403: {"description": "当前用户无权管理注册中心平台"}})
async def get_sync_time(response: Response, service: PlatformDirectoryDependency) -> PlatformSyncTimeResponse:
    response.headers["Cache-Control"] = "no-store"
    return PlatformSyncTimeResponse(next_sync_at=service.get_snapshot().next_sync_at)
