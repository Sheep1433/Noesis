from enum import Enum


class MessageType(Enum):
    INFO = ("info", "信息")
    ERROR = ("error", "错误")


class IntentEnum(Enum):
    """
    意图分类 枚举
    """

    COMMON_QA = ("COMMON_QA", "智能问答")

    SUPER_AGENT_QA = ("SUPER_AGENT_QA", "智能体")

    FAULT_OPERATION_QA = ("FAULT_OPERATION_QA", "故障运维")

    TEST_CASE_QA = ("TEST_CASE_QA", "测试用例生成")



class DataTypeEnum(Enum):
    """
    自定义数据类型枚举
    """

    ANSWER = ("t02", "答案")
    LOCATION = ("t03", "溯源")
    BUS_DATA = ("t04", "业务数据")
    TASK_ID = ("t11", "任务ID,方便后续点赞等操作")
    STREAM_END = ("t99", "流式推流结束")


class DiFyCodeEnum(Enum):
    """
    DiFy 返回数据流定义
    """

    MESSAGE = ("message", "答案")
    MESSAGE_END = ("message_end", "结束")
    MESSAGE_ERROR = ("error", "错误")


class HttpStatusConstant:
    """
    返回状态码

    SUCCESS: 操作成功
    CREATED: 对象创建成功
    ACCEPTED: 请求已经被接受
    NO_CONTENT: 操作已经执行成功，但是没有返回数据
    MOVED_PERM: 资源已被移除
    SEE_OTHER: 重定向
    NOT_MODIFIED: 资源没有被修改
    BAD_REQUEST: 参数列表错误（缺少，格式不匹配）
    UNAUTHORIZED: 未授权
    FORBIDDEN: 访问受限，授权过期
    NOT_FOUND: 资源，服务未找到
    BAD_METHOD: 不允许的http方法
    CONFLICT: 资源冲突，或者资源被锁
    UNSUPPORTED_TYPE: 不支持的数据，媒体类型
    ERROR: 系统内部错误
    NOT_IMPLEMENTED: 接口未实现
    WARN: 系统警告消息
    """

    SUCCESS = 200
    CREATED = 201
    ACCEPTED = 202
    NO_CONTENT = 204
    MOVED_PERM = 301
    SEE_OTHER = 303
    NOT_MODIFIED = 304
    BAD_REQUEST = 400
    UNAUTHORIZED = 401
    FORBIDDEN = 403
    NOT_FOUND = 404
    BAD_METHOD = 405
    CONFLICT = 409
    UNSUPPORTED_TYPE = 415
    ERROR = 500
    NOT_IMPLEMENTED = 501
    WARN = 601