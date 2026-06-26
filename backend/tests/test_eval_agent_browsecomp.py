"""BrowseComp 官方 grader 单元测试（不调 Agent LLM）。"""

import base64
import hashlib

from evals.agent.browsecomp.official import BrowseCompEval, SamplerBase, SamplerResponse, decrypt


class _FixedGrader(SamplerBase):
    def __init__(self, response: str):
        self._response = response

    def __call__(self, message_list):
        return SamplerResponse(self._response, list(message_list), {})


def test_browsecomp_grade_yes():
    ev = BrowseCompEval(_FixedGrader("correct: yes"), examples=[])
    assert ev.grade_sample("Q", "42", "Exact Answer: 42") == "yes"


def test_browsecomp_grade_no():
    ev = BrowseCompEval(_FixedGrader("correct: no"), examples=[])
    assert ev.grade_sample("Q", "42", "Exact Answer: 99") == "no"


def test_decrypt_roundtrip():
    canary = "test-canary"
    plain = "hello browsecomp"

    def derive_key(password: str, length: int) -> bytes:
        hasher = hashlib.sha256()
        hasher.update(password.encode())
        key = hasher.digest()
        return key * (length // len(key)) + key[: length % len(key)]

    key = derive_key(canary, len(plain))
    encrypted = bytes(a ^ b for a, b in zip(plain.encode(), key))
    b64 = base64.b64encode(encrypted).decode()
    assert decrypt(b64, canary) == plain
