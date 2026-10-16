"""持续执行完整入库图，提交待审申请或拒绝反馈后才确认消费。"""

import asyncio
import logging
from collections.abc import Awaitable, Callable

import httpx
from langgraph.runtime import Runtime
from openai import APIStatusError

from app.model.resource import IngestionDelivery
from app.workflow.ingestion import build_ingestion_workflow, nodes
from app.workflow.ingestion.context import IngestionWorkflowContext
from app.workflow.ingestion.state import IngestionWorkflowState

logger = logging.getLogger(__name__)


class IngestionConsumerService:
    def __init__(
        self, context: IngestionWorkflowContext, *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self.context = context
        self._sleep = sleep
        self._task: asyncio.Task | None = None
        # 只保留当前消息契约，不长期持有文件字节、页面或模型对话。
        self._pending: IngestionDelivery | None = None
        self._phase = "initialize"
        self._closed = False
        self.workflow = build_ingestion_workflow({
            "pull_request": self._pull_request,
            "acknowledge_request": self._acknowledge_request,
        })

    async def start(self) -> None:
        if self._closed:
            raise RuntimeError("入库接收服务已关闭")
        if self.context.settings.enabled and self._task is None:
            self._task = asyncio.create_task(self._run(), name="ingestion-consumer")

    async def _pull_request(self, state, runtime: Runtime[IngestionWorkflowContext]):
        if self._pending is None:
            self._phase = "pull"
            result = await nodes.pull_request(state, runtime)
            self._pending = result["delivery"]
        else:
            # 未完成的队首不重新 pull；已提交记录由身份节点恢复，避免重复模型调用。
            result = nodes.initial_delivery_state(self._pending)
        self._phase = "workflow"
        return result

    async def _acknowledge_request(self, state, runtime: Runtime[IngestionWorkflowContext]):
        self._phase = "ack"
        result = await nodes.acknowledge_request(state, runtime)
        self._phase = "workflow"
        return result

    async def _execute(self) -> bool:
        result: IngestionWorkflowState = await self.workflow.ainvoke({}, context=self.context)
        delivery = result["delivery"]
        if delivery is None:
            return False
        if result.get("acknowledged") is not True:
            raise RuntimeError("入库工作流未确认消费，不能处理下一条申请")
        logger.info("入库申请已处理：message_id=%s platform=%s", delivery.message.message_id,
                    delivery.message.platform_code)
        self._pending = None
        return True

    async def process(self, delivery: IngestionDelivery) -> None:
        """处理指定申请；调用者须保证串行执行，令牌始终从会话服务获取。"""
        if self._closed:
            raise RuntimeError("入库接收服务已关闭")
        if self._pending is not None and self._pending != delivery:
            raise RuntimeError("存在尚未完成的入库申请")
        self._pending = delivery
        await self._execute()

    async def _run(self) -> None:
        settings = self.context.settings
        while True:
            try:
                if self.context.sessions.get_access_token() is None:
                    await self._sleep(settings.poll_interval_seconds)
                    continue
                if not await self._execute():
                    await self._sleep(settings.poll_interval_seconds)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                # 模型 SDK 的状态异常不是 HTTPX 异常，仍应保留其 HTTP 状态，不输出响应正文。
                status = exc.response.status_code if isinstance(exc, (httpx.HTTPStatusError, APIStatusError)) else None
                if isinstance(exc, httpx.HTTPStatusError) and status == 409 and self._phase == "ack":
                    # 仅 ACK 冲突允许重新 pull；发布冲突不能当作成功或跳过当前申请。
                    self._pending = None
                logger.warning("入库工作流失败，将重试：phase=%s error_type=%s status=%s",
                               self._phase, type(exc).__name__, status)
                await self._sleep(settings.retry_interval_seconds)

    async def close(self) -> None:
        """停止图及所有子任务；借用的客户端和数据库由应用组合根随后关闭。"""
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
        self._pending = None
