"""按来源平台保存资源，流式写入并原子发布完整文件。"""

import asyncio
import os
import re
import shutil
import stat
import tempfile
from collections.abc import AsyncIterable
from pathlib import Path, PurePosixPath

from app.model.resource import IngestionMessage, StoredFile


def validate_resource_location(platform_code: str, file_name: str) -> None:
    """平台与文件名遵循入库附件契约，不接受 URL、编码路径或目录。"""
    if not isinstance(platform_code, str) or not re.fullmatch(
        r"[a-z][a-z0-9]*(?:-[a-z0-9]+)*", platform_code
    ) or len(platform_code) > 128:
        raise ValueError("资源平台 code 不合法")
    if not isinstance(file_name, str) or not re.fullmatch(
        r"[A-Za-z0-9_-]{1,128}\.[A-Za-z0-9]{1,16}", file_name
    ):
        raise ValueError("资源文件名不合法，请传入不带斜线的附件文件名")


async def file_io(function, *args, **kwargs):
    # 取消时先等待当前磁盘操作结束，再关闭或删除临时文件，避免线程继续写入已关闭句柄。
    task = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        await task
        raise


def sync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ResourceFileStore:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def _prepare_root(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        sync_directory(self.root.parent)

    async def initialize(self) -> None:
        # 启动时准备资源根目录，不依赖中间件登录或首条申请到达。
        await file_io(self._prepare_root)

    def _resolve(self, relative: str) -> Path:
        path = PurePosixPath(relative)
        if path.is_absolute() or ".." in path.parts or len(path.parts) != 2:
            raise ValueError("资源相对路径不合法")
        destination = self.root.joinpath(*path.parts)
        if destination.parent.is_symlink() or destination.is_symlink():
            raise ValueError("资源目录或文件不能是符号链接")
        if not destination.resolve().is_relative_to(self.root):
            raise ValueError("资源路径超出根目录")
        return destination

    def _prepare(self, message: IngestionMessage) -> Path:
        self._prepare_root()
        destination = self._resolve(message.relative_file_path)
        destination.parent.mkdir(exist_ok=True)
        sync_directory(self.root)
        return destination

    async def save(
        self, message: IngestionMessage, chunks: AsyncIterable[bytes], *,
        expected_size: int | None = None,
        overwrite: bool = True,
    ) -> StoredFile:
        destination = await file_io(self._prepare, message)
        descriptor, temporary_name = await file_io(
            tempfile.mkstemp, prefix=f".{message.file.file_id}.", suffix=".part", dir=destination.parent,
        )
        temporary = Path(temporary_name)
        size = 0
        output = os.fdopen(descriptor, "wb")
        try:
            async for chunk in chunks:
                await file_io(output.write, chunk)
                size += len(chunk)
            await file_io(output.flush)
            await file_io(os.fsync, output.fileno())
            result = StoredFile(message.file_url)
            if expected_size is not None and size != expected_size:
                raise ValueError("下载文件长度与响应不一致")
            await file_io(output.close)
            if overwrite:
                await file_io(os.replace, temporary, destination)
            else:
                # 同名重投不能覆盖已保存附件；原子创建使并发竞争者只能复用相同内容。
                await file_io(os.link, temporary, destination)
            await file_io(sync_directory, destination.parent)
            return result
        finally:
            await file_io(output.close)
            await file_io(temporary.unlink, missing_ok=True)

    async def exists(self, source_platform: str, file: StoredFile) -> bool:
        def check():
            validate_resource_location(source_platform, file.file_url)
            return self._resolve(f"{source_platform}/{file.file_url}").is_file()

        return await file_io(check)

    async def read(self, platform_code: str, file_name: str, *, max_file_bytes: int) -> bytes:
        """有界读取完整附件；目录和文件均禁止符号链接，不创建目录或临时文件。"""
        validate_resource_location(platform_code, file_name)
        if max_file_bytes <= 0:
            raise ValueError("文件大小上限必须大于 0")
        return await file_io(self._read, platform_code, file_name, max_file_bytes)

    async def move_to(self, target: "ResourceFileStore", platform_code: str, file_name: str) -> StoredFile:
        """完整发布目标后移除源文件；目标冲突不覆盖，相同内容和已完成移动可重试。"""
        validate_resource_location(platform_code,file_name)
        if self.root == target.root:
            raise ValueError("文件移动的源目录与目标目录不能相同")
        return await file_io(self._move_to,target,platform_code,file_name)

    def _move_to(self, target: "ResourceFileStore", platform_code: str, file_name: str) -> StoredFile:
        source_path = self._resolve(f"{platform_code}/{file_name}")
        target._prepare_root()
        destination = target._resolve(f"{platform_code}/{file_name}")
        destination.parent.mkdir(exist_ok=True)
        sync_directory(target.root)

        def open_regular(path):
            descriptor = os.open(path,os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            stream = os.fdopen(descriptor,"rb")
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                stream.close()
                raise ValueError("只能移动普通附件文件")
            return stream

        def identical(left, right):
            left.seek(0)
            right.seek(0)
            if os.fstat(left.fileno()).st_size != os.fstat(right.fileno()).st_size:
                return False
            while True:
                chunk = left.read(1024*1024)
                if chunk != right.read(1024*1024):
                    return False
                if not chunk:
                    return True

        try:
            source = open_regular(source_path)
        except FileNotFoundError:
            # 上次移动已完成：确认目标为有效普通文件，保持其内容与时间。
            with open_regular(destination):
                return StoredFile(file_name)
        with source:
            temporary = None
            try:
                if destination.exists():
                    with open_regular(destination) as existing:
                        if not identical(source,existing):
                            raise FileExistsError("移动目标已存在不同内容，不能覆盖")
                else:
                    descriptor,name = tempfile.mkstemp(prefix=f".{file_name}.",suffix=".part",dir=destination.parent)
                    temporary = Path(name)
                    with os.fdopen(descriptor,"wb") as output:
                        source.seek(0)
                        shutil.copyfileobj(source,output,length=1024*1024)
                        output.flush()
                        os.fsync(output.fileno())
                    try:
                        os.link(temporary,destination)
                    except FileExistsError:
                        with open_regular(destination) as existing:
                            if not identical(source,existing):
                                raise FileExistsError("并发移动的目标内容不一致") from None
                # 先保证目标完整持久化，再删除源；跨文件系统也不依赖 rename。
                sync_directory(destination.parent)
                try:
                    current = source_path.stat(follow_symlinks=False)
                except FileNotFoundError:
                    current = None
                original = os.fstat(source.fileno())
                if current is not None:
                    if (current.st_dev,current.st_ino) != (original.st_dev,original.st_ino):
                        raise ValueError("移动期间源文件发生变化，不能删除新文件")
                    source_path.unlink()
                    sync_directory(source_path.parent)
                return StoredFile(file_name)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)

    def _read(self, platform_code: str, file_name: str, maximum: int) -> bytes:
        # 使用目录句柄逐级打开，避免检查路径后目录被替换为符号链接的竞态。
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        root_fd = os.open(self.root, directory_flags)
        try:
            platform_fd = os.open(platform_code, directory_flags, dir_fd=root_fd)
            try:
                descriptor = os.open(
                    file_name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                    dir_fd=platform_fd,
                )
                with os.fdopen(descriptor, "rb") as source:
                    info = os.fstat(source.fileno())
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError("资源附件必须为普通文件")
                    if not 0 < info.st_size <= maximum:
                        raise ValueError("资源附件为空或超过文件大小上限")
                    content = source.read(info.st_size + 1)
                    if len(content) != info.st_size or len(content) > maximum:
                        raise ValueError("资源附件读取时长度发生变化或超过上限")
                    return content
            finally:
                os.close(platform_fd)
        finally:
            os.close(root_fd)
