"""手动刷新已有员工姓名、顶级部门，并按需增量同步中央用户名。"""
import asyncio
import logging

import httpx

from app.config.dingtalk import DingTalkSettings
from app.infrastructure.dingtalk import DingTalkClient
from app.infrastructure.middleware import MiddlewareClient
from app.model.department import DepartmentInfo
from app.model.middleware_user import MiddlewareUserPatch
from app.model.personnel_refresh import (EmployeeRefreshChange, DingTalkDepartment,
                                         PersonnelRefreshResult, PersonnelRefreshError)
from app.repository.employee import MySQLEmployeeRepository
from app.repository.personnel_sync import SQLitePersonnelSyncRepository
from app.service.middleware_session import MiddlewareSessionService

logger=logging.getLogger(__name__)


class PersonnelRefreshService:
    def __init__(self,settings: DingTalkSettings,employees: MySQLEmployeeRepository,
                 dingtalk: DingTalkClient | None,pending: SQLitePersonnelSyncRepository,
                 sessions: MiddlewareSessionService,middleware: MiddlewareClient | None):
        self._settings,self._employees,self._dingtalk=settings,employees,dingtalk
        self._pending,self._sessions,self._middleware=pending,sessions,middleware
        self._lock=asyncio.Lock()

    async def _prepare(self):
        targets=await self._employees.list_refresh_targets()
        if len({target.userid for target in targets}) != len(targets):raise ValueError('员工标识重复')
        cache: dict[int,DingTalkDepartment]={}
        cache_lock=asyncio.Lock()
        semaphore=asyncio.Semaphore(self._settings.max_concurrency)

        async def department(department_id):
            async with cache_lock:
                if department_id not in cache:
                    cache[department_id]=await self._dingtalk.get_department(department_id)
                result=cache[department_id]
                if result.dept_id != department_id:raise ValueError('部门详情与请求不一致')
                return result

        async def top_department(ids):
            tops={}
            for department_id in dict.fromkeys(ids):
                current,visited=department_id,set()
                for _ in range(32):
                    if current in visited:raise ValueError('部门层级存在循环')
                    visited.add(current)
                    item=await department(current)
                    if item.dept_id == 1 or item.parent_id == 1:
                        tops[item.dept_id]=item
                        break
                    if item.parent_id == 0:raise ValueError('部门层级未连接企业根部门')
                    current=item.parent_id
                else:raise ValueError('部门层级超出限制')
            # 与知识后端初始化规则一致：根部门与实际一级部门并存时忽略根部门。
            if len(tops)>1:tops.pop(1,None)
            if len(tops)!=1:raise ValueError('员工无法确定唯一顶级部门')
            return next(iter(tops.values()))

        async def prepare(target):
            async with semaphore:
                employee=await self._dingtalk.get_employee(target.userid)
                if employee.userid != target.userid:raise ValueError('员工详情与请求不一致')
                top=await top_department(employee.dept_id_list)
                return EmployeeRefreshChange(before=target,name=employee.name,department_id=top.dept_id),top

        # TaskGroup 任一失败即取消其他读取；全部外部资料通过校验后才进入数据库阶段。
        async with asyncio.TaskGroup() as group:
            tasks=[group.create_task(prepare(target)) for target in targets]
        results=[task.result() for task in tasks]
        changes=[change for change,_ in results]
        departments={top.dept_id:DepartmentInfo(id=top.dept_id,name=top.name) for _,top in results}
        return changes,list(departments.values())

    async def _sync_names(self) -> int:
        jobs=await self._pending.pending()
        if not jobs:return 0
        # 始终发送已持久化的当前姓名，绝不从旧任务保存的载荷恢复旧姓名或权限。
        current={user.userid:user for user in await self._employees.list_refresh_targets()}
        synced=0
        for userid,revision in jobs:
            user=current.get(userid)
            if user is None:
                await self._pending.acknowledge(userid,revision)
                continue
            if user.name is None:continue
            token=self._sessions.get_access_token()
            if token is None or self._middleware is None:continue
            try:
                # 仅发送姓名字段，不携带 permissions，不创建或续期用户登录。
                await self._middleware.update_user_info(MiddlewareUserPatch(userid=userid,username=user.name),token)
            except Exception as error:
                status=error.response.status_code if isinstance(error,httpx.HTTPStatusError) else None
                if status == 401:self._sessions.invalidate_access_token(token)
                logger.warning('员工姓名同步失败：error_type=%s status=%s',type(error).__name__,status)
                continue
            await self._pending.acknowledge(userid,revision)
            synced+=1
        return synced

    async def refresh(self) -> PersonnelRefreshResult:
        if self._dingtalk is None:
            raise PersonnelRefreshError('请先配置钉钉 AppKey 和 AppSecret',phase='configuration')
        if self._lock.locked():
            raise PersonnelRefreshError('已有员工刷新正在执行，请稍后重试',phase='busy',status_code=409)
        phase,database_updated='fetch',False
        try:
            async with self._lock,asyncio.timeout(self._settings.refresh_timeout_seconds):
                changes,departments=await self._prepare()
                phase='prepare_sync'
                # 先登记 userid，再提交 MySQL：提交结果不确定或进程中断后仍可安全重试当前姓名。
                await self._pending.enqueue([change.before.userid for change in changes if change.name != change.before.name])
                phase,database_updated='database',None
                await self._employees.refresh_profiles(changes,departments)
                database_updated=True
                phase='middleware_sync'
                synced=await self._sync_names()
                pending=len(await self._pending.pending())
                if pending:
                    raise PersonnelRefreshError('数据库已更新，部分姓名尚未同步；再次刷新会重试',
                        phase=phase,database_updated=True,pending_names=pending)
                return PersonnelRefreshResult(refreshed_users=len(changes),refreshed_departments=len(departments),
                                              synced_names=synced,pending_names=0)
        except PersonnelRefreshError:raise
        except Exception as error:
            logger.warning('员工资料刷新失败：phase=%s error_type=%s',phase,type(error).__name__)
            message='员工资料刷新超时，请稍后重试' if isinstance(error,TimeoutError) else '员工资料刷新暂不可用，请检查钉钉范围、部门归属及依赖状态'
            raise PersonnelRefreshError(message,phase=phase,database_updated=database_updated) from None
