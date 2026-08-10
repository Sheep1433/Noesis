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
