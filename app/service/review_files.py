"""审核附件、正式资源与删除保留文件的独立目录及移动能力。"""

import asyncio

from app.config.ingestion import IngestionSettings
from app.infrastructure.resource_store import ResourceFileStore
from app.model.resource import StoredFile


class ReviewFileService:
    def __init__(self, settings: IngestionSettings):
        self.resources = ResourceFileStore(settings.resource_root)
        self.ingestion = ResourceFileStore(settings.ingestion_file_root)
        self.deletion = ResourceFileStore(settings.deletion_file_root)
        self._max_file_bytes = settings.max_file_bytes

    async def initialize(self) -> None:
        await asyncio.gather(self.resources.initialize(),self.ingestion.initialize(),self.deletion.initialize())

    async def read_resource(self, platform_code: str, file_name: str) -> bytes:
        """只读取正式资源原件，复用目录校验及文件大小上限，不回退到审核暂存目录。"""
        return await self.resources.read(platform_code, file_name, max_file_bytes=self._max_file_bytes)

    async def read_ingestion(self, platform_code: str, file_name: str) -> bytes:
        """待审附件只从入库暂存目录读取，不回退到正式或删除保留目录。"""
        return await self.ingestion.read(platform_code, file_name, max_file_bytes=self._max_file_bytes)

    async def read_deleted(self, platform_code: str, file_name: str) -> bytes:
        """校验已删除原件在保留目录中的完整性；不回退到正式目录。"""
        return await self.deletion.read(platform_code, file_name, max_file_bytes=self._max_file_bytes)

    async def promote_ingestion(self, platform_code: str, file_name: str) -> StoredFile:
        """供后续批准入库流程调用；仅转移文件，不代替审核或数据库提交。"""
        return await self.ingestion.move_to(self.resources,platform_code,file_name)

    async def retain_deleted(self, platform_code: str, file_name: str) -> StoredFile:
        """供后续正式删除流程调用；保留原文件，不执行最终物理清理。"""
        return await self.resources.move_to(self.deletion,platform_code,file_name)
