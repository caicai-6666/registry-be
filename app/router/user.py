"""注册中心管理员使用的人员与部门查询入口。"""

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from app.model.department import DepartmentInfo
from app.model.employee import EmployeeInfo
from app.repository.employee import MySQLEmployeeRepository
from app.model.personnel_refresh import PersonnelRefreshResult, PersonnelRefreshError
from app.model.user_access import UserAccessPatch, UserAccessState, UserAccessError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user", tags=["user"])


def get_employee_repository(request: Request) -> MySQLEmployeeRepository:
    repository = getattr(request.app.state, "employee_repository", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="人员目录暂不可用",
                            headers={"Cache-Control": "no-store"})
    return repository


EmployeeRepositoryDependency = Annotated[MySQLEmployeeRepository, Depends(get_employee_repository)]


@router.get("/department-list", response_model=list[DepartmentInfo], summary="获取部门列表",
            responses={503: {"description": "人员目录、数据库或部门数据暂不可用"}})
async def list_departments(response: Response, repository: EmployeeRepositoryDependency) -> list[DepartmentInfo]:
    try:
        departments = await repository.list_departments()
    except Exception as error:
        # 数据库故障与非法记录不当作空部门表，也不回显连接信息或上游错误正文。
        logger.warning("部门列表查询失败：error_type=%s", type(error).__name__)
        raise HTTPException(status_code=503, detail="部门列表暂不可用，请稍后重试",
                            headers={"Cache-Control": "no-store"}) from None
    response.headers["Cache-Control"] = "no-store"
    return departments


@router.get("/user-list", response_model=list[EmployeeInfo], summary="获取用户列表",
            responses={503: {"description": "人员目录、数据库或员工数据暂不可用"}})
async def list_users(response: Response, repository: EmployeeRepositoryDependency) -> list[EmployeeInfo]:
    try:
        users = await repository.list_employee_records()
    except Exception as error:
        logger.warning("用户列表查询失败：error_type=%s", type(error).__name__)
        raise HTTPException(status_code=503, detail="用户列表暂不可用，请稍后重试",
                            headers={"Cache-Control": "no-store"}) from None
    response.headers["Cache-Control"] = "no-store"
    return users


@router.post('/refresh-all', response_model=PersonnelRefreshResult, summary='刷新已有员工姓名、部门与任职资料',
             responses={409: {'description': '已有刷新正在执行，或员工资料在读取期间被其他操作修改'},
                        422: {'description': '该接口不接受请求参数或请求体'},
                        503: {'description': '配置、钉钉、数据库或中间件同步失败；详情说明数据库提交状态'}})
async def refresh_all_users(request: Request, response: Response) -> PersonnelRefreshResult:
    headers = {'Cache-Control': 'no-store'}
    if request.query_params or await request.body():
        raise HTTPException(422, '该接口不接受请求参数或请求体', headers=headers)
    service = getattr(request.app.state, 'personnel_refresh_service', None)
    if service is None:
        raise HTTPException(503, '员工刷新服务暂不可用', headers=headers)
    try:
        result = await service.refresh()
    except PersonnelRefreshError as error:
        raise HTTPException(error.status_code, {
            'message': str(error), 'phase': error.phase, 'database_updated': error.database_updated,
            'pending_names': error.pending_names}, headers=headers) from None
    response.headers['Cache-Control'] = 'no-store'
    return result


@router.post('/update-permissions', response_model=UserAccessState, summary='更新用户文件权限和平台管理员关系',
             responses={404: {'description': '目标员工不存在'},
                        409: {'description': '用户权限被并发更新，请重试'},
                        422: {'description': '变更为空、等级/管理员字段非法或平台不在可管理目录中'},
                        503: {'description': '数据库或中间件同步失败，详情说明数据库状态'}})
async def update_user_permissions(body: UserAccessPatch, request: Request, response: Response) -> UserAccessState:
    headers = {'Cache-Control': 'no-store'}
    service = getattr(request.app.state, 'user_access_service', None)
    if service is None:
        raise HTTPException(503, '用户权限管理服务暂不可用', headers=headers)
    try:
        result = await service.update(body)
    except UserAccessError as error:
        raise HTTPException(error.status_code, {
            'message': str(error), 'phase': error.phase, 'database_updated': error.database_updated,
            'pending_updates': error.pending_updates}, headers=headers) from None
    response.headers['Cache-Control'] = 'no-store'
    return result
