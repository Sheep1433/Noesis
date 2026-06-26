"""简易加法 API（含故意注入的 Bug）。"""


def add(a: int, b: int) -> int:
    # BUG: 应为加法
    return a - b
