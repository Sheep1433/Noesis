import pytest
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI

from noesis.llm.factory import ChatOpenAICompatible, StreamIdleTimeoutError


def test_opencode_stream_usage_uses_final_cumulative_chunk() -> None:
    """OpenCode stream usage is cumulative per chunk, not additive across chunks."""
    model = ChatOpenAICompatible(model="deepseek-v4-flash-free", api_key="test-key")
    combined = model._combine_llm_outputs(
        [
            {
                "token_usage": {
                    "prompt_tokens": 1518,
                    "completion_tokens": 1,
                    "total_tokens": 1519,
                }
            },
            {
                "token_usage": {
                    "prompt_tokens": 1518,
                    "completion_tokens": 39,
                    "total_tokens": 1557,
                }
            },
            {
                "token_usage": {
                    "prompt_tokens": 1518,
                    "completion_tokens": 75,
                    "total_tokens": 1593,
                }
            },
        ]
    )

    assert combined["token_usage"] == {
        "prompt_tokens": 1518,
        "completion_tokens": 75,
        "total_tokens": 1593,
    }


def test_opencode_generate_path_does_not_sum_cumulative_chunk_usage(monkeypatch) -> None:
    """The real streaming generation path must retain only the final usage chunk."""
    model = ChatOpenAICompatible(model="deepseek-v4-flash-free", api_key="test-key", streaming=True)
    chunks = [
        ChatGenerationChunk(message=AIMessageChunk(
            content="a",
            usage_metadata={"input_tokens": 1518, "output_tokens": 1, "total_tokens": 1519},
        )),
        ChatGenerationChunk(message=AIMessageChunk(
            content="b",
            usage_metadata={"input_tokens": 1518, "output_tokens": 39, "total_tokens": 1557},
        )),
        ChatGenerationChunk(message=AIMessageChunk(
            content="c",
            usage_metadata={"input_tokens": 1518, "output_tokens": 75, "total_tokens": 1593},
        )),
        ChatGenerationChunk(message=AIMessageChunk(content="")),
    ]

    def fake_stream(*_args, **_kwargs):
        yield from chunks

    monkeypatch.setattr(ChatOpenAI, "_stream", fake_stream)
    result = model._generate_with_cache([HumanMessage(content="hello")])

    assert result.generations[0].message.usage_metadata == {
        "input_tokens": 1518,
        "output_tokens": 75,
        "total_tokens": 1593,
    }


def _assistant_with_reasoning_and_tool_call() -> AIMessage:
    """复现触发 DeepSeek 400 的历史形态：assistant 带 reasoning_content + tool_call。"""
    return AIMessage(
        content="",
        additional_kwargs={
            "reasoning_content": "let me check the weather",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"location":"HZ"}'},
                }
            ],
        },
    )


def test_opencode_deepseek_round_trips_reasoning_content_after_tool_call() -> None:
    """DeepSeek 思考模式下，带 tool_call 的 assistant reasoning_content 必须回传到 API。

    这是导致 ``The `reasoning_content` in the thinking mode must be passed back
    to the API.`` 400 的根因：``langchain_openai`` 序列化时丢弃该字段。
    """
    model = ChatOpenAICompatible(model="deepseek-v4-flash-free", api_key="test-key")
    history = [
        HumanMessage(content="杭州明天天气"),
        _assistant_with_reasoning_and_tool_call(),
        ToolMessage(content="Cloudy 7~13C", tool_call_id="call_1"),
        HumanMessage(content="那后天呢？"),
    ]

    payload = model._get_request_payload(history)

    messages = payload["messages"]
    assistants = [m for m in messages if m["role"] == "assistant"]
    assert len(assistants) == 1
    # reasoning_content 必须出现在 assistant dict 顶层，与 DeepSeek API 契约一致
    assert assistants[0]["reasoning_content"] == "let me check the weather"
    # tool_calls 仍保留，未被破坏
    assert assistants[0]["tool_calls"][0]["function"]["name"] == "get_weather"


def test_opencode_deepseek_injects_each_assistant_reasoning_in_order() -> None:
    """多条 assistant 历史，按顺序对齐回填各自的 reasoning_content。"""
    model = ChatOpenAICompatible(model="deepseek-chat", api_key="test-key")
    history = [
        HumanMessage(content="q1"),
        AIMessage(content="a1", additional_kwargs={"reasoning_content": "think-1"}),
        HumanMessage(content="q2"),
        AIMessage(content="a2", additional_kwargs={"reasoning_content": "think-2"}),
    ]

    payload = model._get_request_payload(history)

    assistants = [m for m in payload["messages"] if m["role"] == "assistant"]
    assert [m["reasoning_content"] for m in assistants] == ["think-1", "think-2"]


def test_opencode_deepseek_skips_assistant_without_reasoning() -> None:
    """无 reasoning_content 的 assistant 不注入字段，避免向 API 传空值。"""
    model = ChatOpenAICompatible(model="deepseek-chat", api_key="test-key")
    history = [
        HumanMessage(content="hi"),
        AIMessage(content="hello"),
        HumanMessage(content="bye"),
    ]

    payload = model._get_request_payload(history)

    assistants = [m for m in payload["messages"] if m["role"] == "assistant"]
    assert len(assistants) == 1
    assert "reasoning_content" not in assistants[0]


def test_compatible_injects_reasoning_for_all_assistants() -> None:
    """所有 assistant 消息无差别回传 reasoning_content。

    不再按模型名判断——响应里产出 reasoning 的模型 additional_kwargs 有值，
    无差别回传对不需要该字段的 API 安全（忽略），对 DeepSeek 思考模式必需。
    """
    model = ChatOpenAICompatible(model="mimo-7b", api_key="test-key")
    history = [
        HumanMessage(content="q"),
        AIMessage(content="a", additional_kwargs={"reasoning_content": "think"}),
    ]

    payload = model._get_request_payload(history)

    assistants = [m for m in payload["messages"] if m["role"] == "assistant"]
    assert len(assistants) == 1
    assert assistants[0]["reasoning_content"] == "think"


# ============ 推理档位（reasoning_effort）注入 ============

from unittest.mock import patch  # noqa: E402


def test_build_chat_model_injects_reasoning_effort_for_openai_family() -> None:
    """openai 协议族（openai/minimax/opencode/deepseek）注入顶层 reasoning_effort。"""
    from noesis.llm.factory import build_chat_model

    for model_type in ("openai", "minimax", "opencode", "deepseek"):
        for effort in ("low", "medium", "high"):
            model = build_chat_model(
                model_type=model_type,
                model_name="deepseek-v4-flash",
                temperature=0.7,
                model_base_url="https://opencode.ai/zen/v1",
                model_api_key="test-key",
                reasoning_effort=effort,
            )
            assert model.reasoning_effort == effort, (model_type, effort)


def test_build_chat_model_skips_reasoning_for_qwen() -> None:
    """qwen 走 DashScope 专有参数体系（enable_thinking），不注入通用 reasoning_effort。

    anthropic 分支同理跳过注入，但其真构造在测试环境不可行（httpx Timeout
    类型不兼容，与档位改动无关），由 model_map 代码结构与 qwen 用例共同覆盖。
    """
    from noesis.llm.factory import build_chat_model

    model = build_chat_model(
        model_type="qwen",
        model_name="qwen-plus",
        temperature=0.7,
        model_base_url="",
        model_api_key="test-key",
        reasoning_effort="high",
    )
    assert getattr(model, "reasoning_effort", None) is None


@patch("noesis.llm.factory.build_chat_model")
@patch("noesis.llm.catalog.resolve_catalog_entry")
def test_get_llm_applies_contextvar_effort_without_capability_gate(
    mock_resolve, mock_build
) -> None:
    """无能力门控：ContextVar 档位一律生效（不支持的端点自行忽略）；显式参数优先。"""
    from types import SimpleNamespace

    from noesis.llm.catalog import ModelCatalogEntry
    from noesis.llm.factory import get_llm
    from noesis.llm.reasoning import (
        clear_request_reasoning_effort,
        set_request_reasoning_effort,
    )

    mock_resolve.return_value = ModelCatalogEntry(
        id="deepseek-v4-flash-free",
        label="Flash",
        model_type="opencode",
        temperature=0.7,
        base_url="https://opencode.ai/zen/v1",
    )
    mock_build.return_value = SimpleNamespace()

    captured: dict[str, object] = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return mock_build.return_value

    mock_build.side_effect = _capture

    try:
        # ContextVar 档位直接透传，任何模型无门控
        set_request_reasoning_effort("high")
        get_llm(model_id="deepseek-v4-flash-free")
        assert captured["reasoning_effort"] == "high"

        # 显式参数优先于 ContextVar
        get_llm(model_id="deepseek-v4-flash-free", reasoning_effort="max")
        assert captured["reasoning_effort"] == "max"
    finally:
        clear_request_reasoning_effort()


@patch("noesis.llm.factory.build_chat_model")
def test_get_llm_ignores_effort_for_summarization(mock_build) -> None:
    """summarization 分支不吃档位。"""
    from noesis.llm.factory import get_llm
    from noesis.llm.reasoning import (
        clear_request_reasoning_effort,
        set_request_reasoning_effort,
    )

    captured: dict[str, object] = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return mock_build.return_value

    mock_build.side_effect = _capture

    try:
        set_request_reasoning_effort("high")
        with patch("noesis.llm.factory.ModelConfig") as mock_cfg:
            mock_cfg.model_type = "opencode"
            mock_cfg.model_name = "kilo-auto/free"
            mock_cfg.model_temperature = "0.7"
            mock_cfg.model_base_url = "https://opencode.ai/zen/v1"
            mock_cfg.model_api_key = "sk-test"
            mock_cfg.summarization_model_name = "sum-model"
            mock_cfg.summarization_model_temperature = 0.3
            mock_cfg.max_retries = 1
            get_llm(purpose="summarization")
        assert captured["reasoning_effort"] is None
    finally:
        clear_request_reasoning_effort()



# ---------------------------------------------------------------------------
# _astream 流级空闲超时：网关挂流（只发 SSE ping 不出内容）时唯一能收口的层
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_astream_idle_timeout_raises_on_hung_stream() -> None:
    """无 chunk 产出超过 request_timeout → StreamIdleTimeoutError（ping 续不了命）。"""
    import asyncio as _asyncio
    from unittest.mock import patch as _patch

    llm = ChatOpenAICompatible(
        model="m", base_url="http://localhost:1/v1", api_key="k",
        max_retries=0, timeout=5, streaming=True,
    )

    async def hung_parent_stream(*args, **kwargs):
        # 模拟挂死流：永不产出 chunk（对应网关只发 SSE 注释帧）
        await _asyncio.sleep(30)
        yield ChatGenerationChunk(message=AIMessageChunk(content="never"))

    from types import SimpleNamespace as _NS
    import noesis.llm.factory as factory_mod
    with _patch.object(ChatOpenAI, "_astream", hung_parent_stream), \
         _patch.object(factory_mod, "ModelConfig", _NS(request_timeout=0.2)):
        with pytest.raises(StreamIdleTimeoutError):
            async for _ in llm.astream("hi"):
                pass


@pytest.mark.asyncio
async def test_astream_idle_timeout_reset_by_real_chunks() -> None:
    """真实 chunk 到达会重置计时器：慢速但活着的流不被误杀。"""
    import asyncio as _asyncio
    from unittest.mock import patch as _patch

    llm = ChatOpenAICompatible(
        model="m", base_url="http://localhost:1/v1", api_key="k",
        max_retries=0, timeout=5, streaming=True,
    )

    async def slow_parent_stream(*args, **kwargs):
        # 每 0.1s 一个 chunk 共 1s：远超单次空闲窗口 0.3s，但每次都有 chunk 续命
        for i in range(10):
            await _asyncio.sleep(0.1)
            yield ChatGenerationChunk(message=AIMessageChunk(content=f"c{i}"))

    from types import SimpleNamespace as _NS
    import noesis.llm.factory as factory_mod
    with _patch.object(ChatOpenAI, "_astream", slow_parent_stream), \
         _patch.object(factory_mod, "ModelConfig", _NS(request_timeout=0.3)):
        chunks = [c async for c in llm.astream("hi")]
    # langchain stream reducer 结尾会追加聚合 chunk，>=10 即证明全部收到、未被误杀
    assert len(chunks) >= 10
