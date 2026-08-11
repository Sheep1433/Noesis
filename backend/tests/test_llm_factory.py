from langchain_core.messages import AIMessageChunk, HumanMessage
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
