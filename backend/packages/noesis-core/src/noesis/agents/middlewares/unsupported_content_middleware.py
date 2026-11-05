"""不支持图片输入的模型：请求侧过滤图片内容块，线程状态保留原文。

会话中途切换模型（VL ↔ 非 VL）时，历史消息里 VL 轮注入的图片内容块
（``image_url`` / ``image``）会随每次模型请求原样发给当前模型——严格的
OpenAI 兼容端点直接 400 整轮失败，宽容网关也白白传输 base64。本中间件在
模型调用边界把当前模型不支持的图片块替换为文本占位符（模型知道自己
错过了什么），消息状态不动：切回 VL 模型时图片自动恢复生效。

能力判定按模型名 hints（``model_name_supports_vision``，请求时从
``request.model`` 取名字，覆盖运行中切模型）；取不到模型名时放行
（fail-open，与上游 deepagents UnsupportedContentMiddleware「profile
缺失按支持处理」语义一致）。参照其设计（请求侧过滤 + 状态保留 +
占位符告知），按 Noesis 的名字判定与块形态适配。
"""

from __future__ import annotations

from typing import Any, Callable

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ContextT,
    ModelRequest,
    ModelResponse,
    ResponseT,
)
from langchain_core.messages import AnyMessage, HumanMessage, ToolMessage

from noesis.llm.vision_meta import model_name_supports_vision

# 两种图片块形态：input_resolver 注入的 OpenAI 式 image_url；
# deepagents read_file 二进制返回的 image（base64 + mime_type）
_IMAGE_BLOCK_TYPES = frozenset({"image", "image_url"})


def _model_reference_name(model: Any) -> str:
    """从模型实例取可判定的名字（ChatOpenAI 系是 .model_name，部分 wrapper 是 .model）。"""
    for attr in ("model_name", "model"):
        value = getattr(model, attr, None)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _image_placeholder(block: dict, message: AnyMessage, model_name: str) -> dict:
    """图片块替换占位符：告知模型存在图片内容但未随请求发送。"""
    mime = str(block.get("mime_type") or "")
    path = (getattr(message, "additional_kwargs", None) or {}).get("read_file_path")
    where = f" {path}" if path else ""
    mime_note = f"（mime={mime}）" if mime else ""
    return {
        "type": "text",
        "text": (
            f"[图片内容{where}未随请求发送：当前模型 {model_name} 不支持图片输入{mime_note}。]"
        ),
    }


class UnsupportedContentMiddleware(AgentMiddleware[AgentState[ResponseT], ContextT, ResponseT]):
    """模型调用边界的图片内容过滤（挂载在中间件栈最末，见 stack.py）。"""

    def _filter_message(self, message: AnyMessage, *, model_name: str) -> AnyMessage:
        content = message.content
        if not isinstance(content, list):
            return message
        new_blocks: list[Any] = []
        changed = False
        for block in content:
            if isinstance(block, dict) and block.get("type") in _IMAGE_BLOCK_TYPES:
                new_blocks.append(_image_placeholder(block, message, model_name))
                changed = True
            else:
                new_blocks.append(block)
        if not changed:
            return message
        return message.model_copy(update={"content": new_blocks})

    def _filter_request(self, request: ModelRequest[ContextT]) -> ModelRequest[ContextT]:
        model = request.model
        model_name = _model_reference_name(model)
        if not model_name or model_name_supports_vision(model_name):
            return request
        messages: list[AnyMessage] = []
        changed = False
        for message in request.messages:
            # 图片块只出现在 human 输入与工具结果；AIMessage 的内容是
            # 自产文本/工具调用，不在过滤面内
            if isinstance(message, (HumanMessage, ToolMessage)):
                filtered = self._filter_message(message, model_name=model_name)
                changed = changed or filtered is not message
                messages.append(filtered)
            else:
                messages.append(message)
        return request.override(messages=messages) if changed else request

    def wrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT]], ModelResponse[ResponseT]],
    ) -> ModelResponse[ResponseT]:
        return handler(self._filter_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[ContextT],
        handler: Callable[[ModelRequest[ContextT], Any]],
    ) -> ModelResponse[ResponseT]:
        return await handler(self._filter_request(request))


__all__ = ["UnsupportedContentMiddleware"]
