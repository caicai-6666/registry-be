"""手动刷新员工资料与在职状态，在数据库提交后增量同步中央资料。"""
import asyncio
import logging

import httpx

from app.config.dingtalk import DingTalkSettings
from app.infrastructure.dingtalk import DingTalkClient, DingTalkEmployeeNotFound
from app.infrastructure.middleware import MiddlewareClient
from app.model.department import DepartmentInfo
from app.model.employee import EmployeeWorkProfile
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
            if not tops:raise ValueError('员工没有可用的顶级部门')
            return [tops[key] for key in sorted(tops)]

        async def prepare(target):
            async with semaphore:
                try:
                    employee=await self._dingtalk.get_employee(target.userid)
                except DingTalkEmployeeNotFound:
                    # 明确不存在转入离职状态更新，不扩大到权限、网络或格式故障。
                    return None
                if employee.userid != target.userid:raise ValueError('员工详情与请求不一致')
                top=await top_department(employee.dept_id_list)
                # 汇报关系使用员工详情的主管 userid，与部门负责人独立；空白资料归一为空。
                work_profile=EmployeeWorkProfile(manager_userid=employee.manager_userid,title=employee.title)
                return EmployeeRefreshChange(before=target,name=employee.name,department_id=top[0].dept_id,
                                             department_ids=[item.dept_id for item in top],
                                             work_profile=work_profile),top

        # TaskGroup 任一失败即取消其他读取；全部外部资料通过校验后才进入数据库阶段。
        async with asyncio.TaskGroup() as group:
            tasks=[group.create_task(prepare(target)) for target in targets]
        results=[task.result() for task in tasks if task.result() is not None]
        resigned=[target for target,task in zip(targets,tasks) if task.result() is None]
        changes=[change for change,_ in results]
        departments={top.dept_id:DepartmentInfo(id=top.dept_id,name=top.name) for _,tops in results for top in tops}
        return changes,list(departments.values()),resigned

    async def _sync_names(self) -> int:
        jobs=await self._pending.pending()
        if not jobs:return 0
        # 始终回读已持久化的姓名、部门和在职状态；待同步标记不保存可能过期的资料。
        current={user.userid:user for user in await self._employees.list_refresh_targets()}
        departments={item.id:item.name for item in await self._employees.list_departments()}
        synced=0
        for userid,revision in jobs:
            user=current.get(userid)
            if user is None:
                await self._pending.acknowledge(userid,revision)
                continue
            token=self._sessions.get_access_token()
            if token is None or self._middleware is None:continue
            try:
                if user.employment_status == 'resigned':
                    # 离职只同步当前权威状态，不重发保留的旧姓名或部门资料。
                    update=MiddlewareUserPatch(userid=userid,employment_status='resigned')
                else:
                    if user.name is None:continue
                    # 中央使用名称而非ID；缺失部门不能静默丢弃，以免错误清空归属。
                    ids=sorted(set(user.department_ids or [user.department_id]))
                    names=list(dict.fromkeys(departments[key].strip() for key in ids))
                    update=MiddlewareUserPatch(userid=userid,username=user.name,departments=names,
                                               employment_status='employed')
                # 不携带 permissions，不创建、撤销或续期登录；确认状态一致后才移除任务。
                await self._middleware.update_user_info(update,token)
            except Exception as error:
                status=error.response.status_code if isinstance(error,httpx.HTTPStatusError) else None
                if status == 401:self._sessions.invalidate_access_token(token)
                logger.warning('员工资料及在职状态同步失败：error_type=%s status=%s',type(error).__name__,status)
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
                changes,departments,resigned=await self._prepare()
                phase='prepare_sync'
                # 离职员工也登记持久任务，避免数据库已标记离职但中央仍为在职。
                # 先登记再提交MySQL；重试始终发送当前权威资料，不恢复旧载荷。
                await self._pending.enqueue([change.before.userid for change in changes]+[target.userid for target in resigned])
                phase,database_updated='database',None
                if changes or resigned:
                    await self._employees.refresh_profiles(changes,departments,resigned)
                database_updated=True
                phase='middleware_sync'
                synced=await self._sync_names()
                pending=len(await self._pending.pending())
                if pending:
                    raise PersonnelRefreshError('数据库已更新，部分资料或在职状态尚未同步；再次刷新会重试',
                        phase=phase,database_updated=True,pending_names=pending)
                return PersonnelRefreshResult(refreshed_users=len(changes),refreshed_departments=len(departments),
                                              synced_names=synced,pending_names=0,skipped_users=len(resigned))
        except PersonnelRefreshError:raise
        except Exception as error:
            logger.warning('员工资料刷新失败：phase=%s error_type=%s',phase,type(error).__name__)
            message='员工资料刷新超时，请稍后重试' if isinstance(error,TimeoutError) else '员工资料刷新暂不可用，请检查钉钉范围、部门归属及依赖状态'
            raise PersonnelRefreshError(message,phase=phase,database_updated=database_updated) from None
