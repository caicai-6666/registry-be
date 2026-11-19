"""平台首次发现、人工决策标记与权限发布共享 MySQL 事务边界。"""
import json
import re
from uuid import uuid4

from app.model.user_access import UserAccessState


async def lock_permission_gate(cursor) -> bool:
    """先锁 gate 再读取员工，跨后端统一锁顺序；缺少迁移时拒绝继续。"""
    await cursor.execute('SELECT baseline_ready FROM platform_permission_control WHERE id=1 FOR UPDATE')
    row = await cursor.fetchone()
    if row is None:
        raise RuntimeError('权限初始化控制行缺失，请先完成迁移')
    return bool(row['baseline_ready'])


async def enqueue_permissions(cursor, userid: str) -> None:
    await cursor.execute('INSERT INTO platform_permission_outbox(userid,revision) VALUES(%s,%s) '
                         'ON DUPLICATE KEY UPDATE revision=VALUES(revision)', (userid, uuid4().hex))


class PlatformPermissionRepository:
    def __init__(self, employees):
        self.employees = employees

    async def discover(self, codes: list[str]) -> None:
        codes = sorted(set(codes))
        if not codes or any(not re.fullmatch(r'[a-z][a-z0-9]*(?:-[a-z0-9]+)*', code)
                            or len(code) > 128 for code in codes):
            # 空目录不能作为上线基线，否则下一次全部平台会被误认成新平台。
            raise ValueError('平台权限初始化要求完整非空平台目录')
        async with self.employees.permission_transaction() as cursor:
            ready = await lock_permission_gate(cursor)
            await cursor.execute('SELECT platform_code FROM platform_permission_registry FOR UPDATE')
            known = {row['platform_code'] for row in await cursor.fetchall()}
            new = set(codes) - known
            if not new and ready:
                return
            for code in sorted(new):
                await cursor.execute('INSERT INTO platform_permission_registry(platform_code,is_baseline) VALUES(%s,%s)',
                                     (code, not ready))
            if not ready:
                await cursor.execute('UPDATE platform_permission_control SET baseline_ready=TRUE WHERE id=1')
                return
            # 登记、整批补权和 outbox 同事务提交，失败重跑不会出现半批复活撤权。
            await cursor.execute("SELECT userid,platform_permissions FROM employees WHERE employment_status='employed' "
                                 'ORDER BY userid FOR UPDATE')
            employees = await cursor.fetchall()
            await cursor.execute('SELECT userid,platform_code FROM platform_permission_overrides FOR UPDATE')
            overrides = {(row['userid'], row['platform_code']) for row in await cursor.fetchall()}
            for row in employees:
                state = UserAccessState(userid=row['userid'], platform_permissions=json.loads(row['platform_permissions']),
                                        managed_platforms=[])
                permissions = dict(state.platform_permissions)
                for code in sorted(new):
                    if code not in permissions and (state.userid, code) not in overrides:
                        permissions[code] = 0
                if permissions != state.platform_permissions:
                    await cursor.execute('UPDATE employees SET platform_permissions=%s WHERE userid=%s',
                                         (json.dumps(permissions, ensure_ascii=False), state.userid))
                    await enqueue_permissions(cursor, state.userid)

    async def pending(self) -> list[str]:
        async with self.employees.permission_transaction() as cursor:
            await cursor.execute('SELECT userid FROM platform_permission_outbox ORDER BY userid')
            return [row['userid'] for row in await cursor.fetchall()]

    async def publish(self, userid: str, send, *, timeout_seconds: float, only_pending: bool = True) -> None:
        # 持有 gate 跨越读取和发布，避免人工撤权/知识后端全表发布被迟到旧快照覆盖。
        async with self.employees.permission_transaction(timeout_seconds=timeout_seconds) as cursor:
            await lock_permission_gate(cursor)
            await cursor.execute('SELECT revision FROM platform_permission_outbox WHERE userid=%s FOR UPDATE', (userid,))
            pending = await cursor.fetchone()
            if only_pending and pending is None:
                return
            await cursor.execute('SELECT userid,platform_permissions FROM employees WHERE userid=%s FOR UPDATE', (userid,))
            row = await cursor.fetchone()
            if row is not None:
                state = UserAccessState(userid=userid, platform_permissions=json.loads(row['platform_permissions']), managed_platforms=[])
                await send(state.platform_permissions)
            if pending:
                await cursor.execute('DELETE FROM platform_permission_outbox WHERE userid=%s AND revision=%s',
                                     (userid, pending['revision']))
