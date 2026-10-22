"""人员目录中的头像引用转换；注册中心只展示文件名，不存储或读取头像。"""

import re


def avatar_response_filename(relative_url: str | None) -> str | None:
    if relative_url is None:
        return None
    if not isinstance(relative_url, str) or re.fullmatch(r'user/avatar/[a-f0-9]{32}\.webp', relative_url) is None:
        raise ValueError('头像存储引用不合法')
    return relative_url.rsplit('/', 1)[1]
