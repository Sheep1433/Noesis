"""测试点覆盖 recall/precision：确定性 token 对齐 + 可选 LLM 仲裁 borderline。"""

from __future__ import annotations

import json
import os
import re
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Tuple

# 过泛的中文词，不参与 borderline / 文档支撑判定
_GENERIC_TOKENS = frozenset(
    {
        "测试",
        "功能",
        "流程",
        "场景",
        "用户",
        "系统",
        "接口",
        "页面",
        "提示",
        "校验",
        "登录",
        "提交",
        "支持",
        "正常",
        "异常",
        "成功",
        "失败",
        "返回",
        "显示",
        "包含",
        "进行",
        "可以",
        "需要",
        "应当",
        "必须",
    }
)


class MatchLevel(str, Enum):
    CERTAIN = "certain"
    BORDERLINE = "borderline"
    NONE = "none"


def extract_tokens(text: str, *, drop_generic: bool = True) -> set[str]:
    tokens: set[str] = set()
    raw = text or ""

    for m in re.finditer(r"[A-Za-z][A-Za-z0-9_]{1,}|\d+", raw):
        tok = m.group().lower()
        if not drop_generic or tok not in _GENERIC_TOKENS:
            tokens.add(tok)

    for part in re.split(r"[\s、，。；：「」\[\]()（）/\\|]+", raw):
        chunk = re.sub(r"[^\u4e00-\u9fff]", "", part)
        if len(chunk) >= 2 and (not drop_generic or chunk not in _GENERIC_TOKENS):
            tokens.add(chunk)

    for chunk in re.findall(r"[\u4e00-\u9fff]+", raw):
        if 2 <= len(chunk) <= 8 and (not drop_generic or chunk not in _GENERIC_TOKENS):
            tokens.add(chunk)
        for i in range(len(chunk) - 1):
            bg = chunk[i : i + 2]
            if drop_generic and bg in _GENERIC_TOKENS:
                continue
            tokens.add(bg)
    return tokens


def match_level(golden_name: str, gen_name: str) -> MatchLevel:
    """判断生成测试点是否覆盖金标准测试点（语义近似）。"""
    g_norm = re.sub(r"\s+", "", golden_name or "")
    p_norm = re.sub(r"\s+", "", gen_name or "")
    if len(g_norm) >= 4 and g_norm in p_norm:
        return MatchLevel.CERTAIN
    if len(p_norm) >= 4 and p_norm in g_norm:
        return MatchLevel.CERTAIN

    g_tok = extract_tokens(golden_name)
    p_tok = extract_tokens(gen_name)
    if not g_tok or not p_tok:
        return MatchLevel.NONE

    inter = g_tok & p_tok
    if not inter:
        return MatchLevel.NONE

    ratio = len(inter) / len(g_tok)
    if len(inter) >= 3 or (len(inter) >= 2 and ratio >= 0.35):
        return MatchLevel.CERTAIN
    if len(inter) >= 2 or any(len(t) >= 4 for t in inter):
        return MatchLevel.BORDERLINE
    return MatchLevel.NONE


def flatten_generated_points(scenes: Sequence[Dict[str, Any]]) -> List[str]:
    names: List[str] = []
    for scene in scenes:
        for tp in scene.get("test_points") or []:
            if isinstance(tp, dict):
                name = str(tp.get("point_name") or "").strip()
            else:
                name = str(tp).strip()
            if name:
                names.append(name)
    return names


def _parse_golden_points(raw: Any) -> List[Dict[str, str]]:
    if isinstance(raw, list):
        data = raw
    elif isinstance(raw, str) and raw.strip():
        data = json.loads(raw)
    else:
        return []
    out: List[Dict[str, str]] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        scene = str(item.get("scene_name") or "").strip()
        point = str(item.get("point_name") or "").strip()
        if point:
            out.append({"scene_name": scene, "point_name": point})
    return out


def _borderline_llm_enabled() -> bool:
    return os.environ.get("NOESIS_CASE_COVERAGE_LLM_BORDERLINE", "1").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def _llm_arbitrate(*, golden_name: str, gen_name: str, mode: str) -> bool:
    """borderline 对：由 LLM 判定是否语义覆盖（recall）或是否有效（precision）。"""
    from llm import get_llm

    if mode == "recall":
        question = (
            f"金标准测试点：{golden_name}\n"
            f"生成测试点：{gen_name}\n"
            "生成测试点是否在 point_name 语义上覆盖金标准所表达的 PRD 需求？"
            "scene 名不一致可接受，但须反映同一需求点。仅回答 yes 或 no。"
        )
    else:
        question = (
            f"生成测试点：{gen_name}\n"
            f"参考金标准测试点：{golden_name}\n"
            "生成测试点是否语义上能对应这条金标准，或明显被同一 PRD 需求支持？"
            "仅回答 yes 或 no。"
        )

    llm = get_llm()
    text = str(llm.invoke(question).content or "").strip().lower()
    return text.startswith("yes") or text.startswith("是") or "yes" in text[:12]


def _pair_covers(golden_name: str, gen_name: str, *, use_llm: bool) -> bool:
    level = match_level(golden_name, gen_name)
    if level == MatchLevel.CERTAIN:
        return True
    if level == MatchLevel.BORDERLINE and use_llm:
        try:
            return _llm_arbitrate(golden_name=golden_name, gen_name=gen_name, mode="recall")
        except Exception:
            return False
    return False


def _point_valid_for_precision(
    gen_name: str,
    golden_points: Sequence[Dict[str, str]],
    document_text: str,
    *,
    use_llm: bool,
) -> Tuple[bool, str]:
    for g in golden_points:
        gname = g["point_name"]
        level = match_level(gname, gen_name)
        if level == MatchLevel.CERTAIN:
            return True, f"匹配金标准「{gname}」"
        if level == MatchLevel.BORDERLINE and use_llm:
            try:
                if _llm_arbitrate(golden_name=gname, gen_name=gen_name, mode="precision"):
                    return True, f"LLM 仲裁有效（参考「{gname}」）"
            except Exception:
                pass

    p_tok = extract_tokens(gen_name)
    d_tok = extract_tokens(document_text, drop_generic=True)
    inter = p_tok & d_tok
    if len(inter) >= 2:
        return True, f"PRD 文档支撑（关键词：{', '.join(sorted(inter)[:4])}）"
    return False, "未匹配金标准且 PRD 支撑不足"


def score_point_coverage_recall(
    scenes: Sequence[Dict[str, Any]],
    golden_points: Sequence[Dict[str, str]],
    *,
    use_llm_borderline: Optional[bool] = None,
) -> Dict[str, Any]:
    generated = flatten_generated_points(scenes)
    golden = list(golden_points)
    if not golden:
        return {"score": 0.0, "covered": 0, "total": 0, "reason": "金标准为空"}
    if not generated:
        return {
            "score": 0.0,
            "covered": 0,
            "total": len(golden),
            "reason": "生成测试点为空",
        }

    use_llm = _borderline_llm_enabled() if use_llm_borderline is None else use_llm_borderline
    covered = 0
    missed: List[str] = []
    for g in golden:
        gname = g["point_name"]
        if any(_pair_covers(gname, pname, use_llm=use_llm) for pname in generated):
            covered += 1
        else:
            missed.append(gname)

    score = round(covered / len(golden), 4)
    reason = f"覆盖 {covered}/{len(golden)} 条金标准"
    if missed:
        reason += f"；未覆盖示例：{missed[0]}"
        if len(missed) > 1:
            reason += f" 等 {len(missed)} 条"
    return {
        "score": score,
        "covered": covered,
        "total": len(golden),
        "missed": missed,
        "reason": reason,
    }


def score_point_coverage_precision(
    scenes: Sequence[Dict[str, Any]],
    golden_points: Sequence[Dict[str, str]],
    document_text: str = "",
    *,
    use_llm_borderline: Optional[bool] = None,
) -> Dict[str, Any]:
    generated = flatten_generated_points(scenes)
    golden = list(golden_points)
    if not generated:
        return {"score": 0.0, "valid": 0, "total": 0, "reason": "生成测试点为空"}

    use_llm = _borderline_llm_enabled() if use_llm_borderline is None else use_llm_borderline
    valid = 0
    invalid: List[str] = []
    for pname in generated:
        ok, _why = _point_valid_for_precision(
            pname, golden, document_text, use_llm=use_llm
        )
        if ok:
            valid += 1
        else:
            invalid.append(pname)

    score = round(valid / len(generated), 4)
    reason = f"有效 {valid}/{len(generated)} 条生成测试点"
    if invalid:
        reason += f"；无效示例：{invalid[0]}"
        if len(invalid) > 1:
            reason += f" 等 {len(invalid)} 条"
    return {
        "score": score,
        "valid": valid,
        "total": len(generated),
        "invalid": invalid,
        "reason": reason,
    }


def parse_golden_from_context(context: Dict[str, Any]) -> List[Dict[str, str]]:
    vars_ = context.get("vars") or {}
    return _parse_golden_points(vars_.get("golden_test_points_json"))


def scenes_from_run_output(run_output: Dict[str, Any]) -> List[Dict[str, Any]]:
    state = run_output.get("state") or {}
    scenes = state.get("scenes_testpoints") or []
    return scenes if isinstance(scenes, list) else []
