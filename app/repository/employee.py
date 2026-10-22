"""注册中心人员目录与管理员关联查询；首次员工初始化由知识后端负责。"""

import asyncio
import json

import aiomysql

from app.auth.model import AuthenticatedUser
from app.config.database import MySQLSettings, load_mysql_settings
from app.model.avatar import avatar_response_filename
from app.model.platform_admin import PlatformAdminProfile
from app.model.department import DepartmentInfo
from app.model.employee import EmployeeInfo
from app.model.personnel_refresh import EmployeeRefreshTarget, EmployeeRefreshChange, PersonnelRefreshError
from app.model.user_access import UserAccessPatch, UserAccessState, UserAccessError, apply_access_patch


_EMPLOYEE_SELECT = (
    'SELECT e.userid,e.name,e.avatar_url,e.department_id,e.platform_permissions,d.name AS department_name, '
    # 同一条查询读取员工与管理员关联，避免文件权限等级被误用为管理身份。
    'COALESCE((SELECT JSON_ARRAYAGG(a.platform_code) FROM platform_admins AS a '
    'WHERE a.employee_userid=e.userid),JSON_ARRAY()) AS managed_platforms '
    'FROM employees AS e JOIN departments AS d ON d.id=e.department_id'
)
_EMPLOYEE_QUERY = _EMPLOYEE_SELECT + ' WHERE e.userid=%s'


def _user(row: dict) -> AuthenticatedUser:
    return AuthenticatedUser(userid=row['userid'], name=row['name'],
        avatar_url=avatar_response_filename(row['avatar_url']), department_id=row['department_id'],
        department_name=row['department_name'], platform_permissions=json.loads(row['platform_permissions']),
        managed_platforms=json.loads(row['managed_platforms']))


class MySQLEmployeeRepository:
    def __init__(self, settings: MySQLSettings | None = None):
        self._settings = settings
        self._pool: aiomysql.Pool | None = None
        self._lock = asyncio.Lock()

    async def _initialize(self) -> None:
        async with self._lock:
            if self._pool is not None:
                return
            settings = self._settings or load_mysql_settings()
            self._settings = settings
            async with asyncio.timeout(settings.operation_timeout_seconds):
                self._pool = await aiomysql.create_pool(
                    host=settings.host, port=settings.port, user=settings.user,
                    password=settings.password.get_secret_value(), db=settings.database,
                    charset="utf8mb4", autocommit=True, minsize=1, maxsize=5,
                    connect_timeout=settings.connect_timeout_seconds, pool_recycle=300,
                )

    async def find_by_userid(self, userid: str) -> AuthenticatedUser | None:
        await self._initialize()
        async with asyncio.timeout(self._settings.operation_timeout_seconds):
            async with self._pool.acquire() as connection, connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(_EMPLOYEE_QUERY, (userid,))
                row = await cursor.fetchone()
        if row is None:
            return None
        return _user(row)

    async def list_departments(self) -> list[DepartmentInfo]:
        """读取已登记部门，包含暂无员工的部门；失败不能伪装为空列表。"""
        await self._initialize()
        async with asyncio.timeout(self._settings.operation_timeout_seconds):
            async with self._pool.acquire() as connection, connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute('SELECT id,name FROM departments ORDER BY id')
                rows = await cursor.fetchall()
        return [DepartmentInfo.model_validate(row) for row in rows]

    async def list_employee_records(self) -> list[EmployeeInfo]:
        """仅读取 employees 五个字段，部门与管理员信息由调用方另行关联。"""
        await self._initialize()
        async with asyncio.timeout(self._settings.operation_timeout_seconds):
            async with self._pool.acquire() as connection, connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute('SELECT userid,name,avatar_url,department_id,platform_permissions '
                                     'FROM employees ORDER BY userid')
                rows = await cursor.fetchall()
        return [EmployeeInfo(userid=row['userid'], name=row['name'],
                             avatar_url=avatar_response_filename(row['avatar_url']),
                             department_id=row['department_id'],
                             platform_permissions=json.loads(row['platform_permissions'])) for row in rows]

    async def list_refresh_targets(self) -> list[EmployeeRefreshTarget]:
        """刷新只读取 userid、姓名和部门，不依赖头像或权限的解析。"""
        await self._initialize()
        async with asyncio.timeout(self._settings.operation_timeout_seconds):
            async with self._pool.acquire() as connection, connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute('SELECT userid,name,department_id FROM employees ORDER BY userid')
                return [EmployeeRefreshTarget.model_validate(row) for row in await cursor.fetchall()]

    async def refresh_profiles(self, changes: list[EmployeeRefreshChange], departments: list[DepartmentInfo]) -> None:
        """同一事务更新已有员工及部门名称；禁止插入员工、覆盖权限、头像或管理员关系。"""
        await self._initialize()
        async with asyncio.timeout(self._settings.operation_timeout_seconds):
            async with self._pool.acquire() as connection:
                await connection.begin()
                try:
                    async with connection.cursor(aiomysql.DictCursor) as cursor:
                        await cursor.execute('SELECT userid,name,department_id FROM employees ORDER BY userid FOR UPDATE')
                        current = {row['userid']: EmployeeRefreshTarget.model_validate(row) for row in await cursor.fetchall()}
                        # 外部资料读取期间员工若被编辑或删除，本次重试，避免覆盖更新后的资料。
                        if any(current.get(change.before.userid) != change.before for change in changes):
                            raise PersonnelRefreshError('员工资料在刷新期间发生变化，请重新刷新',
                                phase='database', database_updated=False, status_code=409)
                        for department in sorted(departments, key=lambda item: item.id):
                            await cursor.execute('INSERT INTO departments(id,name) VALUES(%s,%s) '
                                                 'ON DUPLICATE KEY UPDATE name=VALUES(name)',
                                                 (department.id, department.name))
                        for change in sorted(changes, key=lambda item: item.before.userid):
                            await cursor.execute('UPDATE employees SET name=%s,department_id=%s WHERE userid=%s',
                                                 (change.name, change.department_id, change.before.userid))
                    await connection.commit()
                except BaseException:
                    await asyncio.shield(connection.rollback())
                    raise

    @staticmethod
    async def _read_user_access(cursor, userid: str, *, for_update: bool = False) -> UserAccessState:
        suffix = ' FOR UPDATE' if for_update else ''
        await cursor.execute('SELECT userid,platform_permissions FROM employees WHERE userid=%s' + suffix, (userid,))
        row = await cursor.fetchone()
        if row is None:
            raise UserAccessError('员工不存在', status_code=404)
        await cursor.execute('SELECT platform_code FROM platform_admins WHERE employee_userid=%s ORDER BY platform_code', (userid,))
        admins = [item['platform_code'] for item in await cursor.fetchall()]
        return UserAccessState(userid=row['userid'], platform_permissions=json.loads(row['platform_permissions']),
                               managed_platforms=admins)

    async def get_user_access(self, userid: str) -> UserAccessState:
        await self._initialize()
        async with asyncio.timeout(self._settings.operation_timeout_seconds):
            async with self._pool.acquire() as connection, connection.cursor(aiomysql.DictCursor) as cursor:
                return await self._read_user_access(cursor, userid)

    async def update_user_access(self, patch: UserAccessPatch, expected: UserAccessState) -> UserAccessState:
        """加锁校验后在同一事务更新权限及管理员关联，不覆盖未指定的平台。"""
        await self._initialize()
        async with asyncio.timeout(self._settings.operation_timeout_seconds):
            async with self._pool.acquire() as connection:
                await connection.begin()
                try:
                    async with connection.cursor(aiomysql.DictCursor) as cursor:
                        current = await self._read_user_access(cursor, patch.userid, for_update=True)
                        if current != expected:
                            raise UserAccessError('用户权限在更新期间发生变化，请重试', status_code=409)
                        updated = apply_access_patch(current, patch)
                        if updated.platform_permissions != current.platform_permissions:
                            await cursor.execute('UPDATE employees SET platform_permissions=%s WHERE userid=%s',
                                (json.dumps(updated.platform_permissions, ensure_ascii=False), patch.userid))
                        old_admins = set(current.managed_platforms)
                        for code, enabled in sorted(patch.platform_admins.items()):
                            if enabled and code not in old_admins:
                                await cursor.execute('INSERT INTO platform_admins(platform_code,employee_userid) VALUES(%s,%s)',
                                                     (code, patch.userid))
                            elif not enabled and code in old_admins:
                                await cursor.execute('DELETE FROM platform_admins WHERE platform_code=%s AND employee_userid=%s',
                                                     (code, patch.userid))
                    await connection.commit()
                    return updated
                except BaseException:
                    await asyncio.shield(connection.rollback())
                    raise

    async def list_users(self) -> list[AuthenticatedUser]:
        """读取人员管理目录；查询失败必须上抛，不能伪装为空目录。"""
        await self._initialize()
        async with asyncio.timeout(self._settings.operation_timeout_seconds):
            async with self._pool.acquire() as connection, connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute(_EMPLOYEE_SELECT + ' ORDER BY e.userid')
                rows = await cursor.fetchall()
        return [_user(row) for row in rows]

    async def list_platform_admins(self) -> dict[str, list[str]]:
        """读取人员管理所需的完整管理员关联；查询失败必须上抛。"""
        await self._initialize()
        async with asyncio.timeout(self._settings.operation_timeout_seconds):
            async with self._pool.acquire() as connection, connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute('SELECT platform_code,employee_userid FROM platform_admins '
                                     'ORDER BY platform_code,employee_userid')
                rows = await cursor.fetchall()
        admins: dict[str, list[str]] = {}
        for row in rows:
            admins.setdefault(row['platform_code'], []).append(row['employee_userid'])
        return admins

    async def list_platform_admin_profiles(self) -> dict[str, list[PlatformAdminProfile]]:
        """批量读取管理员名称与头像，头像仅返回经过校验的文件名。"""
        await self._initialize()
        async with asyncio.timeout(self._settings.operation_timeout_seconds):
            async with self._pool.acquire() as connection, connection.cursor(aiomysql.DictCursor) as cursor:
                await cursor.execute('SELECT a.platform_code,e.userid,e.name,e.avatar_url FROM platform_admins AS a '
                                     'JOIN employees AS e ON e.userid=a.employee_userid '
                                     'ORDER BY a.platform_code,e.userid')
                rows = await cursor.fetchall()
        profiles: dict[str, list[PlatformAdminProfile]] = {}
        for row in rows:
            profiles.setdefault(row['platform_code'], []).append(PlatformAdminProfile(
                userid=row['userid'], name=row['name'] or row['userid'],
                avatar_url=avatar_response_filename(row['avatar_url'])))
        return profiles

    async def close(self) -> None:
        if self._pool is not None:
            self._pool.close()
            await self._pool.wait_closed()
            self._pool = None
