"""人工审核共用的业务失败边界。"""


class ReviewNotFound(LookupError):
    pass


class ReviewConflict(ValueError):
    pass


class ReviewUnavailable(RuntimeError):
    pass
