"""同一 worker 的 MLLM 客户端共享在途请求额度。"""

import asyncio


def get_mllm_request_limiter(maximum: int) -> asyncio.BoundedSemaphore:
    loop = asyncio.get_running_loop()
    attribute = "_registry_mllm_request_limit"
    existing = getattr(loop, attribute, None)
    if existing is None:
        existing = (maximum, asyncio.BoundedSemaphore(maximum))
        setattr(loop, attribute, existing)
    if existing[0] != maximum:
        raise ValueError("MLLM 共享并发配置不一致，更改配置后需要重启 worker")
    return existing[1]
