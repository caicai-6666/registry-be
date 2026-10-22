"""中央认证与人员、管理员依赖的稳定错误，不携带凭据或上游正文。"""


class AuthUnavailable(RuntimeError):
    """中央会话、员工目录或管理员服务暂时不可用。"""
