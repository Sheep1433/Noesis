import httpx
from langchain_openai import ChatOpenAI
from langchain_deepseek import ChatDeepSeek
from langchain_qwq import ChatQwen
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessageChunk
from noesis.config.env import ModelConfig
from noesis.runtime.logging import logger

_OPENCODE_DEFAULT_BASE_URL = "https://opencode.ai/zen/v1"
_OPENCODE_DEFAULT_HEADERS = {
    "HTTP-Referer": "https://opencode.ai/",
    "X-Title": "opencode",
}
_DEBUG_TOKEN_USAGE_TAG = "[DEBUG-TOKEN-USAGE]"
_PROVIDER_USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "input_tokens",
    "output_tokens",
    "prompt_tokens_details",
    "completion_tokens_details",
    "input_tokens_details",
    "output_tokens_details",
)


def _debug_provider_usage(value):
    """Log only numeric provider usage fields, never response content."""
    if value is None:
        return {"raw_type": "missing"}
    if hasattr(value, "model_dump"):
        value = value.model_dump()
    elif hasattr(value, "dict"):
        value = value.dict()
    if not isinstance(value, dict):
        return {"raw_type": type(value).__name__}
    fields = {}
    for key in _PROVIDER_USAGE_KEYS:
        item = value.get(key)
        if isinstance(item, dict):
            numeric = {}
            for nested_key, nested_value in item.items():
                try:
                    numeric[nested_key] = int(nested_value)
                except (TypeError, ValueError):
                    continue
            if numeric:
                fields[key] = numeric
        else:
            try:
                fields[key] = int(item)
            except (TypeError, ValueError):
                continue
    return {"raw_type": type(value).__name__, "fields": fields}


class ChatOpenCode(ChatOpenAI):
    """OpenCode Zen 统一适配：归一化不同模型的 reasoning 字段到 additional_kwargs["reasoning_content"]。

    OpenCode 聚合了多家模型，reasoning 字段格式不统一：
    - DeepSeek 系列：delta.reasoning_content（字符串）
    - MiMo 系列：delta.reasoning（字符串）+ delta.reasoning_details（数组，含 type/text/format/index）
    - 其他模型：可能无 reasoning 或用不同字段

    本类在流式和非流式两条路径上统一提取，上层只需读 additional_kwargs["reasoning_content"]。
    """

    @staticmethod
    def _extract_reasoning_from_delta(delta: dict) -> str | None:
        """从流式 delta 字典提取 reasoning 文本，统一输出字符串。"""
        # 1. DeepSeek 原生：reasoning_content（字符串）
        reasoning_content = delta.get("reasoning_content")
        if reasoning_content is not None:
            return reasoning_content
        # 2. MiMo / OpenRouter：reasoning（字符串）
        reasoning = delta.get("reasoning")
        if reasoning is not None:
            return reasoning
        # 3. reasoning_details 数组（MiMo 结构化格式）：拼接 text 字段
        details = delta.get("reasoning_details")
        if isinstance(details, list) and details:
            parts = [d.get("text", "") for d in details if isinstance(d, dict) and d.get("text")]
            if parts:
                return "".join(parts)
        return None

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        """流式：从每个 chunk 的 delta 提取 reasoning 并归一化。"""
        if isinstance(chunk, dict) and chunk.get("usage"):
            logger.debug(
                "{} provider_stream_usage model={} response_id={} usage={}",
                _DEBUG_TOKEN_USAGE_TAG,
                self.model_name,
                chunk.get("id") or "",
                _debug_provider_usage(chunk.get("usage")),
            )
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info,
        )
        choices = chunk.get("choices") if isinstance(chunk, dict) else None
        if choices and generation_chunk and isinstance(generation_chunk.message, AIMessageChunk):
            delta = choices[0].get("delta", {})
            reasoning = self._extract_reasoning_from_delta(delta)
            if reasoning is not None:
                generation_chunk.message.additional_kwargs["reasoning_content"] = reasoning
            # 标记 provider 供上层区分
            generation_chunk.message.response_metadata = {
                **generation_chunk.message.response_metadata,
                "model_provider": "opencode",
            }
        return generation_chunk

    def _combine_llm_outputs(self, llm_outputs: list[dict | None]) -> dict:
        """Use the final cumulative usage instead of summing every stream chunk.

        OpenCode returns prompt/completion usage as a running total on each
        streamed chunk. LangChain's OpenAI implementation sums ``token_usage``
        across chunks, which turns one request into an inflated total.
        """
        final_token_usage = None
        metadata_outputs = []
        for output in llm_outputs:
            if output is None:
                metadata_outputs.append(None)
                continue
            token_usage = output.get("token_usage")
            if token_usage is not None:
                final_token_usage = dict(token_usage)
            metadata_outputs.append(
                {key: value for key, value in output.items() if key != "token_usage"}
            )

        combined = super()._combine_llm_outputs(metadata_outputs)
        if final_token_usage is not None:
            combined["token_usage"] = final_token_usage
        return combined

    def _create_chat_result(self, response, generation_info=None):
        """非流式：从完整 response 提取 reasoning 并归一化。"""
        logger.debug(
            "{} provider_response_usage model={} response_id={} usage={}",
            _DEBUG_TOKEN_USAGE_TAG,
            self.model_name,
            getattr(response, "id", "") or "",
            _debug_provider_usage(getattr(response, "usage", None)),
        )
        rtn = super()._create_chat_result(response, generation_info)
        for generation in rtn.generations:
            if generation.message.response_metadata is None:
                generation.message.response_metadata = {}
            generation.message.response_metadata["model_provider"] = "opencode"

        # 尝试从 response 的 message 里提取 reasoning
        choices = getattr(response, "choices", None) if hasattr(response, "choices") else None
        if choices:
            msg = choices[0].message
            # DeepSeek 原生
            if hasattr(msg, "reasoning_content") and msg.reasoning_content:
                rtn.generations[0].message.additional_kwargs["reasoning_content"] = msg.reasoning_content
            else:
                # OpenRouter / MiMo：reasoning 和 reasoning_details 在 model_extra 里
                model_extra = getattr(msg, "model_extra", None) or {}
                if isinstance(model_extra, dict):
                    delta_like = {
                        "reasoning_content": model_extra.get("reasoning_content"),
                        "reasoning": model_extra.get("reasoning"),
                        "reasoning_details": model_extra.get("reasoning_details"),
                    }
                    reasoning = self._extract_reasoning_from_delta(delta_like)
                    if reasoning is not None:
                        rtn.generations[0].message.additional_kwargs["reasoning_content"] = reasoning
        return rtn


def _llm_http_timeout() -> httpx.Timeout:
    """读超时 = 无响应间隔上限；连接超时单独限制避免内网挂死过久。"""
    read_sec = float(ModelConfig.request_timeout)
    return httpx.Timeout(connect=10.0, read=read_sec, write=read_sec, pool=10.0)


def _llm_http_clients() -> tuple[httpx.Client, httpx.AsyncClient]:
    """
    与 Langfuse OTEL 一致：trust_env=False，不走 macOS/Shell 系统代理（如 10810）。
    避免代理进程挂掉或长连接被掐时 OpenCode/DashScope 报 APIConnectionError。
    """
    timeout = _llm_http_timeout()
    return (
        httpx.Client(timeout=timeout, trust_env=False),
        httpx.AsyncClient(timeout=timeout, trust_env=False),
    )


def build_chat_model(
    *,
    model_type: str,
    model_name: str,
    temperature: float,
    model_base_url: str,
    model_api_key: str,
    provider_max_retries: int | None = None,
):
    timeout = _llm_http_timeout()
    max_retries = (
        int(ModelConfig.max_retries)
        if provider_max_retries is None
        else max(0, int(provider_max_retries))
    )
    http_client, http_async_client = _llm_http_clients()
    http_kwargs = {
        "http_client": http_client,
        "http_async_client": http_async_client,
    }

    model_map = {
        "openai": lambda: ChatOpenAI(
            model=model_name,
            temperature=temperature,
            base_url=model_base_url,
            api_key=model_api_key,
            timeout=timeout,
            max_retries=max_retries,
            streaming=ModelConfig.streaming,
            **http_kwargs,
        ),
        "minimax": lambda: ChatOpenAI(
            model=model_name,
            temperature=temperature,
            base_url=model_base_url,
            api_key=model_api_key,
            timeout=timeout,
            max_retries=max_retries,
            streaming=ModelConfig.streaming,
            **http_kwargs,
        ),
        "opencode": lambda: ChatOpenCode(
            model=model_name,
            temperature=temperature,
            base_url=model_base_url or _OPENCODE_DEFAULT_BASE_URL,
            api_key=model_api_key,
            timeout=timeout,
            max_retries=max_retries,
            streaming=ModelConfig.streaming,
            default_headers=_OPENCODE_DEFAULT_HEADERS,
            **http_kwargs,
        ),
        "qwen": lambda: ChatQwen(
            model=model_name,
            temperature=temperature,
            base_url=model_base_url,
            api_key=model_api_key,
            max_tokens=int(ModelConfig.max_tokens),
            top_p=float(ModelConfig.top_p),
            frequency_penalty=float(ModelConfig.frequency_penalty),
            presence_penalty=float(ModelConfig.presence_penalty),
            timeout=timeout,
            max_retries=max_retries,
            streaming=ModelConfig.streaming,
            **http_kwargs,
        ),
        "deepseek": lambda: ChatDeepSeek(
            model=model_name,
            temperature=temperature,
            base_url=model_base_url,
            api_key=model_api_key,
            timeout=timeout,
            max_retries=max_retries,
            streaming=ModelConfig.streaming,
            **http_kwargs,
        ),
        "anthropic": lambda: ChatAnthropic(
            model=model_name,
            temperature=temperature,
            base_url=model_base_url,
            api_key=model_api_key,
            max_tokens=int(ModelConfig.max_tokens),
            timeout=timeout,
            max_retries=max_retries,
            streaming=ModelConfig.streaming,
            **http_kwargs,
        ),
    }

    if model_type in model_map:
        return model_map[model_type]()
    raise ValueError(
        f"Unsupported MODEL_TYPE: {model_type}. "
        f"Supported types: {', '.join(model_map.keys())}"
    )


def get_llm(purpose: str | None = None, *, model_id: str | None = None):
    from noesis.llm.catalog import resolve_catalog_entry
    from noesis.llm.runtime_snapshot import get_runtime_model_snapshot

    runtime_snapshot = get_runtime_model_snapshot(
        model_id,
        purpose="chat" if purpose in {None, "chat"} else purpose,
    )
    use_summary_model = purpose == "summarization" and bool(
        ModelConfig.summarization_model_name.strip()
    )

    if runtime_snapshot is not None:
        model_type = runtime_snapshot.model_type
        model_name = runtime_snapshot.model_name
        temperature_str = ModelConfig.model_temperature
        model_base_url = runtime_snapshot.base_url
    elif use_summary_model:
        model_type = ModelConfig.model_type.strip().lower()
        model_name = ModelConfig.summarization_model_name.strip()
        temperature_str = str(ModelConfig.summarization_model_temperature)
        model_base_url = ModelConfig.model_base_url
    elif model_id:
        entry = resolve_catalog_entry(model_id)
        model_type = entry.model_type
        model_name = entry.model_name
        temperature_str = str(entry.temperature)
        model_base_url = entry.base_url
    else:
        model_type = ModelConfig.model_type.strip().lower()
        model_name = ModelConfig.model_name
        temperature_str = ModelConfig.model_temperature
        model_base_url = ModelConfig.model_base_url

    model_api_key = runtime_snapshot.api_key if runtime_snapshot is not None else ModelConfig.model_api_key

    if not model_type:
        raise ValueError("MODEL_TYPE environment variable is not set.")

    if not model_api_key:
        raise ValueError("MODEL_API_KEY environment variable is not set.")

    try:
        temperature = float(temperature_str)
    except ValueError:
        raise ValueError(f"Invalid MODEL_TEMPERATURE value: {temperature_str}. Must be a float.")

    return build_chat_model(
        model_type=model_type,
        model_name=model_name,
        temperature=temperature,
        model_base_url=model_base_url,
        model_api_key=model_api_key,
        provider_max_retries=(
            int(ModelConfig.max_retries) if purpose == "summarization" else 0
        ),
    )
