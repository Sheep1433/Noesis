"""非 Vision 主模型时，用独立 VLM 为聊天图片生成文本描述（fallback）。"""
from __future__ import annotations

import base64
from typing import Any

from common.logging import logger

_CHAT_IMAGE_CAPTION_PROMPT = (
    "请用中文简洁描述这张图片的关键信息（主体、文字、图表数据、界面元素等），"
    "供文本问答模型阅读。控制在 300 字以内，不要编造图中不存在的内容。"
)


def _image_data_uri(data: bytes, mime: str) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _extract_message_text(message: Any) -> str:
    """从 chat completion message 提取可用文本（含 reasoning_content 兜底）。"""
    content = (getattr(message, "content", None) or "").strip()
    if content:
        return content

    reasoning = (getattr(message, "reasoning_content", None) or "").strip()
    if reasoning:
        return reasoning

    if hasattr(message, "model_dump"):
        payload = message.model_dump()
        for key in ("content", "reasoning_content", "text"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return ""


def _empty_response_error(model_name: str, finish_reason: Any) -> ValueError:
    return ValueError(
        f"VLM 返回空描述 (model={model_name!r}, finish_reason={finish_reason!r})"
    )


def describe_image_bytes_for_chat(
    data: bytes,
    mime: str,
    *,
    file_name: str = "",
) -> str:
    """
    同步调用配置的 VLM 生成图片描述。未配置 VLM 或调用失败时抛出异常，由调用方降级。
    """
    from kb.embedding import is_vlm_configured

    if not is_vlm_configured():
        raise ValueError("VLM 未配置")

    import httpx
    from openai import OpenAI

    from config.env import ModelConfig

    model_name = (ModelConfig.vlm_model_name or "").strip()
    if not model_name:
        raise ValueError("VLM 未配置")

    api_key = ModelConfig.vlm_model_api_key.strip()
    data_uri = _image_data_uri(data, mime or "image/jpeg")
    client = OpenAI(
        api_key=api_key,
        base_url=ModelConfig.vlm_model_base_url,
        http_client=httpx.Client(
            timeout=httpx.Timeout(connect=10, read=120, write=30, pool=10),
        ),
    )
    label = file_name or "图片"
    logger.info(
        f"[vlm_caption] 开始描述聊天图片 file_name={label!r} "
        f"bytes={len(data)} model={model_name!r}"
    )

    try:
        response = client.chat.completions.create(
            model=model_name,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _CHAT_IMAGE_CAPTION_PROMPT},
                        {"type": "image_url", "image_url": {"url": data_uri}},
                    ],
                }
            ],
            max_tokens=512,
            temperature=0.2,
            extra_body={"enable_thinking": False},
        )
        choice = response.choices[0]
        text = _extract_message_text(choice.message)
        if not text:
            raise _empty_response_error(
                model_name, getattr(choice, "finish_reason", None)
            )
        logger.info(
            f"[vlm_caption] 完成 file_name={label!r} model={model_name!r} "
            f"desc_len={len(text)}"
        )
        return text
    except Exception as exc:
        logger.warning(
            f"[vlm_caption] model={model_name!r} 失败: {type(exc).__name__}: {exc}"
        )
        raise
