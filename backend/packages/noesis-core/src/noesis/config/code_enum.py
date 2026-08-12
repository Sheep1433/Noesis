"""意图分类、HTTP 状态码常量。"""


from enum import Enum


class IntentEnum(Enum):
    """意图分类枚举。"""

    COMMON_QA = ("COMMON_QA", "智能问答")
    SUPER_AGENT_QA = ("SUPER_AGENT_QA", "智能体")
    FAULT_OPERATION_QA = ("FAULT_OPERATION_QA", "故障运维")
    TEST_CASE_QA = ("TEST_CASE_QA", "测试用例生成")


class HttpStatusConstant:
    """ResponseUtil 使用的 HTTP 状态码常量。"""

    SUCCESS = 200
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    CONFLICT = 409
    TOO_MANY_REQUESTS = 429
    ERROR = 500
    SERVICE_UNAVAILABLE = 503
    WARN = 601
