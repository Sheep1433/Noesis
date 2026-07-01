def num_tokens_from_string(text: str) -> int:
    return max(len(text or "") // 4, 1)
