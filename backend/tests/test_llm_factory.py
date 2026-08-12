from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGenerationChunk
from langchain_openai import ChatOpenAI

from noesis.llm.factory import ChatOpenCode


def test_opencode_stream_usage_uses_final_cumulative_chunk() -> None:
    """OpenCode stream usage is cumulative per chunk, not additive across chunks."""
    model = ChatOpenCode(model="deepseek-v4-flash-free", api_key="test-key")
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
    model = ChatOpenCode(model="deepseek-v4-flash-free", api_key="test-key", streaming=True)
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
    model = ChatOpenCode(model="deepseek-v4-flash-free", api_key="test-key")
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
    model = ChatOpenCode(model="deepseek-chat", api_key="test-key")
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
    model = ChatOpenCode(model="deepseek-chat", api_key="test-key")
    history = [
        HumanMessage(content="hi"),
        AIMessage(content="hello"),
        HumanMessage(content="bye"),
    ]

    payload = model._get_request_payload(history)

    assistants = [m for m in payload["messages"] if m["role"] == "assistant"]
    assert len(assistants) == 1
    assert "reasoning_content" not in assistants[0]


def test_opencode_non_deepseek_model_does_not_inject_reasoning() -> None:
    """非 DeepSeek 模型不注入 reasoning_content（其他 provider 无此回传契约）。"""
    model = ChatOpenCode(model="mimo-7b", api_key="test-key")
    history = [
        HumanMessage(content="q"),
        AIMessage(content="a", additional_kwargs={"reasoning_content": "think"}),
    ]

    payload = model._get_request_payload(history)

    assistants = [m for m in payload["messages"] if m["role"] == "assistant"]
    assert len(assistants) == 1
    assert "reasoning_content" not in assistants[0]

