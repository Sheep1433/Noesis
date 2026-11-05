"""UnsupportedContentMiddleware 契约：非 VL 模型的请求侧图片过滤。

锁定的场景：会话中 VL 轮注入的图片块（image_url / image）留在消息状态里，
切到非 VL 模型后每次请求都会带上——本中间件在模型调用边界把它们替换为
文本占位符，消息状态不动（切回 VL 模型图片自动恢复）。
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from noesis.agents.middlewares import UnsupportedContentMiddleware
from noesis.agents.middlewares.stack import build_noesis_stack
from noesis.agents.middlewares.stack import NoesisStackDeps


class _Model:
    def __init__(self, name: str | None) -> None:
        if name is not None:
            self.model_name = name


class _Request:
    """最小请求替身：中间件只读 .model/.messages，写经 .override。"""

    def __init__(self, model: _Model, messages: list[Any]) -> None:
        self.model = model
        self.messages = messages

    def override(self, messages: list[Any]) -> "_Request":
        return _Request(self.model, messages)


def _vl_human() -> HumanMessage:
    return HumanMessage(
        content=[
            {"type": "text", "text": "这张图里是什么"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]
    )


def test_non_vision_model_filters_image_blocks_with_notice() -> None:
    original = _vl_human()
    request = _Request(_Model("glm-4.6"), [original])
    filtered = UnsupportedContentMiddleware()._filter_request(request)  # type: ignore[arg-type]
    assert filtered is not request
    block = filtered.messages[0].content[1]
    assert block["type"] == "text"
    assert "不支持图片输入" in block["text"]
    assert "glm-4.6" in block["text"]
    # 状态保留：原消息对象不被原地改写
    assert original.content[1]["type"] == "image_url"
    # 文本块原样保留
    assert filtered.messages[0].content[0] == {"type": "text", "text": "这张图里是什么"}


def test_vision_model_keeps_image_blocks() -> None:
    request = _Request(_Model("qwen-vl-max"), [_vl_human()])
    filtered = UnsupportedContentMiddleware()._filter_request(request)  # type: ignore[arg-type]
    assert filtered is request, "VL 模型不得改写请求"


def test_model_without_name_fails_open() -> None:
    request = _Request(_Model(None), [_vl_human()])
    filtered = UnsupportedContentMiddleware()._filter_request(request)  # type: ignore[arg-type]
    assert filtered is request, "取不到模型名时放行（fail-open）"


def test_read_file_image_block_filtered_with_path() -> None:
    message = ToolMessage(
        content=[{"type": "image", "base64": "AAAA", "mime_type": "image/png"}],
        name="read_file",
        tool_call_id="call-1",
        additional_kwargs={"read_file_path": "/workspace/shot.png"},
    )
    request = _Request(_Model("glm-4.6"), [message])
    filtered = UnsupportedContentMiddleware()._filter_request(request)  # type: ignore[arg-type]
    text = filtered.messages[0].content[0]["text"]
    assert "/workspace/shot.png" in text
    assert "image/png" in text


def test_ai_message_and_plain_text_untouched() -> None:
    ai = AIMessage(content=[{"type": "image_url", "image_url": {"url": "data:;base64,BBBB"}}])
    human = HumanMessage(content="纯文本消息")
    request = _Request(_Model("glm-4.6"), [ai, human])
    filtered = UnsupportedContentMiddleware()._filter_request(request)  # type: ignore[arg-type]
    assert filtered is request, "AIMessage 与纯文本不在过滤面内"


def test_wrap_model_call_applies_filter_and_state_survives_roundtrip() -> None:
    """契约主场景：非 VL 轮过滤、状态不动；切回 VL 轮原样发送。"""
    mw = UnsupportedContentMiddleware()
    state = [_vl_human()]

    seen: list[list[Any]] = []

    def handler(request: _Request) -> str:
        seen.append(request.messages)
        return "ok"

    mw.wrap_model_call(_Request(_Model("glm-4.6"), list(state)), handler)  # type: ignore[arg-type]
    assert seen[0][0].content[1]["type"] == "text", "非 VL 轮：请求侧已替换"

    mw.wrap_model_call(_Request(_Model("qwen-vl-max"), list(state)), handler)  # type: ignore[arg-type]
    assert seen[1][0].content[1]["type"] == "image_url", "切回 VL：状态原文照发"


def test_stack_mounts_before_hitl() -> None:
    """挂载位置契约：在 HITL 之前（HITL 保持最内层不变），覆盖无 HITL 时垫底。"""
    stack = build_noesis_stack(NoesisStackDeps(profile="COMMON_QA"))
    assert isinstance(stack[-1], UnsupportedContentMiddleware), "无 HITL 时图片过滤垫底"

    stack_hitl = build_noesis_stack(
        NoesisStackDeps(profile="COMMON_QA", interrupt_on={"execute": True})
    )
    names = [type(item).__name__ for item in stack_hitl]
    assert names[-1] == "HumanInTheLoopMiddleware", "HITL 最内层契约不变"
    assert names[-2] == "UnsupportedContentMiddleware"
