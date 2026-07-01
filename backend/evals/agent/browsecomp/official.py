"""
openai/simple-evals BrowseComp（MIT）— 单文件 vendored。
https://github.com/openai/simple-evals/blob/main/browsecomp_eval.py
"""

from __future__ import annotations

import base64
import hashlib
import os
import random
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx
import pandas

BROWSECOMP_CSV_URL = (
    "https://openaipublic.blob.core.windows.net/simple-evals/browse_comp_test_set.csv"
)
BROWSECOMP_CSV_CACHE = Path(__file__).resolve().parent / "data" / "browse_comp_test_set.csv"

Message = dict[str, Any]
MessageList = list[Message]

QUERY_TEMPLATE = """
{Question}

Your response should be in the following format:
Explanation: {{your explanation for your final answer}}
Exact Answer: {{your succinct, final answer}}
Confidence: {{your confidence score between 0% and 100% for your answer}}
""".strip()

GRADER_TEMPLATE = """
Judge whether the following [response] to [question] is correct or not based on the precise and unambiguous [correct_answer] below.

[question]: {question}

[response]: {response}

Your judgement must be in the format and criteria specified below:

extracted_final_answer: The final exact answer extracted from the [response]. Put the extracted answer as 'None' if there is no exact, final answer to extract from the response.

[correct_answer]: {correct_answer}

reasoning: Explain why the extracted_final_answer is correct or incorrect based on [correct_answer], focusing only on if there are meaningful differences between [correct_answer] and the extracted_final_answer. Do not comment on any background to the problem, do not attempt to solve the problem, do not argue for any answer different than [correct_answer], focus only on whether the answers match.

correct: Answer 'yes' if extracted_final_answer matches the [correct_answer] given above, or is within a small margin of error for numerical problems. Answer 'no' otherwise, i.e. if there if there is any inconsistency, ambiguity, non-equivalency, or if the extracted answer is incorrect.

confidence: The extracted confidence score between 0% and 100% from [response]. Put 100 if there is no confidence score available.
""".strip()


@dataclass
class SamplerResponse:
    response_text: str
    actual_queried_message_list: MessageList
    response_metadata: dict[str, Any]


class SamplerBase:
    def __call__(self, message_list: MessageList) -> SamplerResponse:
        raise NotImplementedError

    def _pack_message(self, content: Any, role: str) -> Message:
        return {"role": str(role), "content": content}


@dataclass
class EvalResult:
    score: float | None
    metrics: dict[str, float] | None
    htmls: list[str]
    convos: list[MessageList]
    metadata: dict[str, Any] | None


@dataclass
class SingleEvalResult:
    score: float | None
    metrics: dict[str, float] = field(default_factory=dict)
    html: str | None = None
    convo: MessageList | None = None
    example_level_metadata: dict[str, Any] | None = None


def derive_key(password: str, length: int) -> bytes:
    hasher = hashlib.sha256()
    hasher.update(password.encode())
    key = hasher.digest()
    return key * (length // len(key)) + key[: length % len(key)]


def decrypt(ciphertext_b64: str, password: str) -> str:
    encrypted = base64.b64decode(ciphertext_b64)
    key = derive_key(password, len(encrypted))
    return bytes(a ^ b for a, b in zip(encrypted, key)).decode()


def resolve_browsecomp_csv_path(csv_url: str | None = None) -> Path:
    """解析 BrowseComp CSV 路径：环境变量 / 本地缓存 / 远程下载。"""
    env_path = (os.environ.get("BROWSECOMP_CSV_PATH") or "").strip()
    if env_path:
        path = Path(env_path).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"BROWSECOMP_CSV_PATH 不存在: {path}")
        return path

    if csv_url and not csv_url.startswith(("http://", "https://")):
        path = Path(csv_url).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"BrowseComp CSV 不存在: {path}")
        return path

    if BROWSECOMP_CSV_CACHE.is_file():
        return BROWSECOMP_CSV_CACHE

    remote_url = csv_url or BROWSECOMP_CSV_URL
    try:
        BROWSECOMP_CSV_CACHE.parent.mkdir(parents=True, exist_ok=True)
        with httpx.Client(timeout=120.0, follow_redirects=True) as client:
            resp = client.get(remote_url)
            resp.raise_for_status()
            BROWSECOMP_CSV_CACHE.write_bytes(resp.content)
        return BROWSECOMP_CSV_CACHE
    except Exception as e:
        raise RuntimeError(
            "无法下载 BrowseComp 数据集（DNS/网络问题或目标不可达）。\n"
            f"  URL: {remote_url}\n"
            f"  原因: {e}\n"
            "请检查网络后重试，或手动下载 CSV 到：\n"
            f"  {BROWSECOMP_CSV_CACHE}\n"
            "也可设置环境变量 BROWSECOMP_CSV_PATH 指向本地文件。"
        ) from e


def _map_items(f: Callable, xs: list[Any]) -> list[Any]:
    if not xs:
        return []
    if os.getenv("debug") or len(xs) == 1:
        return [f(x) for x in xs]
    return [f(x) for x in xs]


def _aggregate(results: list[SingleEvalResult]) -> EvalResult:
    name2values: dict[str, list[float]] = defaultdict(list)
    convos: list[MessageList] = []
    metadata: list = []
    for row in results:
        for name, value in row.metrics.items():
            name2values[name].append(float(value))
        if row.convo:
            convos.append(row.convo)
        if row.example_level_metadata:
            metadata.append(row.example_level_metadata)
    metrics = {k: sum(v) / len(v) for k, v in name2values.items() if v}
    return EvalResult(
        score=metrics.get("is_correct"),
        metrics=metrics or None,
        htmls=[],
        convos=convos,
        metadata={"per_example": metadata} if metadata else None,
    )


class BrowseCompEval:
    def __init__(
        self,
        grader_model: SamplerBase,
        num_examples: int | None = None,
        examples: list[dict] | None = None,
        csv_url: str | None = None,
    ):
        if examples is not None:
            rows = examples
        else:
            csv_path = resolve_browsecomp_csv_path(csv_url)
            df = pandas.read_csv(csv_path)
            rows = [row.to_dict() for _, row in df.iterrows()]
        if num_examples:
            rows = random.Random(0).sample(rows, num_examples)
        self.examples = rows
        self.grader_model = grader_model

    def grade_sample(self, question: str, correct_answer: str, response: str) -> str:
        grader_prompt = GRADER_TEMPLATE.format(
            question=question,
            correct_answer=correct_answer,
            response=response,
        )
        out = self.grader_model([self.grader_model._pack_message(grader_prompt, "user")])
        match = re.search(r"correct:\s*(yes|no)", out.response_text, re.IGNORECASE)
        return match.group(1).lower() if match else "no"

    def __call__(self, sampler: SamplerBase) -> EvalResult:
        def one(row: dict) -> SingleEvalResult:
            problem = decrypt(row.get("problem", ""), row.get("canary", ""))
            answer = decrypt(row.get("answer", ""), row.get("canary", ""))
            prompt = [sampler._pack_message(QUERY_TEMPLATE.format(Question=problem), "user")]
            out = sampler(prompt)
            grade = self.grade_sample(problem, answer, out.response_text)
            ok = grade == "yes"
            return SingleEvalResult(
                score=float(ok),
                convo=out.actual_queried_message_list + [{"role": "assistant", "content": out.response_text}],
                metrics={"is_correct": float(ok), "is_incorrect": float(not ok)},
                example_level_metadata={"problem": problem, "grade": grade},
            )

        return _aggregate(_map_items(one, self.examples))
