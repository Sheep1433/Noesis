import json
from typing import Any
import httpx
from langchain_openai import ChatOpenAI
from langchain_deepseek import ChatDeepSeek
from langchain_qwq import ChatQwen
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, AIMessageChunk
from noesis.config.env import ModelConfig

def _opencode_preset() -> tuple[str, dict[str, str]]:
    """opencode 便捷默认值来自 config.yaml model.provider_presets（部署者维护）。"""
    for preset in ModelConfig.provider_presets:
        if preset.get("id") == "opencode":
            return str(preset.get("base_url") or ""), dict(preset.get("headers") or {})
    return "", {}


def _reasoning_to_text(reasoning: Any) -> str:
    """Best-effort 把 reasoning 载荷（str/list/dict）转成可读文本。

    参考 deer-flow vllm_provider：reasoning 可能是字符串、结构化数组或字典，
    需递归提取 text/content 字段，最后兜底 JSON 序列化。
    """
    if reasoning is None:
        return ""
    if isinstance(reasoning, str):
        return reasoning
    if isinstance(reasoning, list):
        return "".join(_reasoning_to_text(item) for item in reasoning)
    if isinstance(reasoning, dict):
        for key in ("text", "content", "reasoning"):
            value = reasoning.get(key)
            if isinstance(value, str):
                return value
            if value is not None:
                text = _reasoning_to_text(value)
                if text:
                    return text
        try:
            return json.dumps(reasoning, ensure_ascii=False)
        except TypeError:
            return str(reasoning)
    try:
        return json.dumps(reasoning, ensure_ascii=False)
    except TypeError:
        return str(reasoning)


class ChatOpenAICompatible(ChatOpenAI):
    """OpenAI 兼容端点统一适配：自动探测并归一化 reasoning 字段。

    reasoning 是模型能力（DeepSeek/MiMo 等支持思考的模型会产出），不是 vendor
    能力——同一 OpenAI 协议端点后端可能是支持思考的模型，也可能是普通模型。
    因此本类对所有响应都尝试提取 reasoning，响应无相关字段时返回 None 不影响。

    兼容的字段格式：
    - DeepSeek 系列：delta.reasoning_content（字符串）
    - MiMo / vLLM 系列：delta.reasoning（字符串或结构化）+ delta.reasoning_details（数组）
    - 其他模型：无 reasoning 字段，自动跳过

    在流式和非流式两条路径上统一提取，同时保留原始 reasoning 与归一化文本
    reasoning_content，上层读 additional_kwargs["reasoning_content"] 即可。
    序列化方向对所有 assistant 消息无差别回传 reasoning（API 不需要时忽略，
    DeepSeek 思考模式多轮 tool call 需要时自动生效）。参考 deer-flow vllm_provider。
    """

    @staticmethod
    def _extract_reasoning_from_delta(delta: dict) -> str | None:
        """从流式 delta 字典提取 reasoning 文本，统一输出字符串。"""
        # 1. DeepSeek 原生：reasoning_content（字符串）
        reasoning_content = delta.get("reasoning_content")
        if reasoning_content is not None:
            return _reasoning_to_text(reasoning_content)
        # 2. MiMo / vLLM / OpenRouter：reasoning（字符串或结构化）
        reasoning = delta.get("reasoning")
        if reasoning is not None:
            return _reasoning_to_text(reasoning)
        # 3. reasoning_details 数组（MiMo 结构化格式）：拼接 text 字段
        details = delta.get("reasoning_details")
        if isinstance(details, list) and details:
            parts = [d.get("text", "") for d in details if isinstance(d, dict) and d.get("text")]
            if parts:
                return "".join(parts)
        return None

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        """流式：从每个 chunk 的 delta 提取 reasoning 并归一化。"""
        generation_chunk = super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info,
        )
        choices = chunk.get("choices") if isinstance(chunk, dict) else None
        if choices and generation_chunk and isinstance(generation_chunk.message, AIMessageChunk):
            delta = choices[0].get("delta", {})
            reasoning = self._extract_reasoning_from_delta(delta)
            if reasoning is not None:
                # 同时保留归一化文本与原始字段，便于序列化回传与上游消费
                generation_chunk.message.additional_kwargs["reasoning_content"] = reasoning
                if delta.get("reasoning") is not None:
                    generation_chunk.message.additional_kwargs["reasoning"] = delta.get("reasoning")
            # 标记 provider 供上层区分
            generation_chunk.message.response_metadata = {
                **generation_chunk.message.response_metadata,
                "model_provider": "opencode",
            }
        return generation_chunk

    def _stream(self, *args, **kwargs):
        """Keep only the final cumulative usage in LangChain's stream reducer.

        OpenCode repeats the running ``prompt_tokens``/``completion_tokens``
        total on every chunk. ``generate_from_stream`` adds
        ``AIMessageChunk.usage_metadata`` across chunks, so passing those
        values through makes one request look like dozens of requests.
        Keep one chunk buffered and strip usage from every preceding chunk;
        the final provider chunk remains authoritative.
        """
        pending = None
        last_usage = None
        for chunk in super()._stream(*args, **kwargs):
            usage = getattr(chunk.message, "usage_metadata", None)
            if usage is not None:
                last_usage = usage
            if pending is not None:
                message = pending.message
                if getattr(message, "usage_metadata", None) is not None:
                    pending = pending.model_copy(update={
                        "message": message.model_copy(update={"usage_metadata": None}),
                    })
                yield pending
            pending = chunk
        if pending is not None:
            if getattr(pending.message, "usage_metadata", None) is None and last_usage is not None:
                pending = pending.model_copy(update={
                    "message": pending.message.model_copy(update={"usage_metadata": last_usage}),
                })
            yield pending

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

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        """序列化方向：把 assistant 的 reasoning_content 回传到 API。

        DeepSeek 思考模式要求，一旦某轮发生了 tool call，该 assistant 的
        ``reasoning_content`` 必须在后续所有 turn 的上下文中原样回传，否则
        返回 400 ``The `reasoning_content` in the thinking mode must be passed
        back to the API.``。``langchain_openai`` 的 ``_convert_message_to_dict``
        不认识 ``reasoning_content``，序列化时直接丢弃；``langchain_deepseek``
        也只在捕获方向写入、序列化方向未补。

        本类对所有 assistant 消息无差别回传 reasoning_content（参考 deer-flow
        vllm_provider）：API 不需要该字段时会忽略，需要它的模型（DeepSeek 思考
        模式多轮 tool call）自动生效。无需按模型名判断——响应里没产出 reasoning
        的模型，``additional_kwargs`` 本就为空，不会注入。

        实现要点：``_convert_message_to_dict`` 转出的 dict 已丢失
        ``additional_kwargs["reasoning_content"]``，因此先从原始 AIMessage
        列表按序提取，再按 assistant 出现顺序对齐回填到 dict 列表。
        """
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        messages = payload.get("messages")
        if not isinstance(messages, list):
            return payload

        # 原始消息按 assistant 顺序提取 reasoning_content，与 dict 列表中
        # assistant 顺序一一对应（chat/completions 分支保持输入顺序）。
        original = self._convert_input(input_).to_messages()
        reasoning_queue = [
            msg.additional_kwargs.get("reasoning_content")
            for msg in original
            if isinstance(msg, AIMessage)
        ]
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            if message.get("reasoning_content"):
                continue
            if not reasoning_queue:
                break
            reasoning = reasoning_queue.pop(0)
            if reasoning:
                message["reasoning_content"] = reasoning
        return payload


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
    reasoning_effort: str | None = None,
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
    # 流式模式下，OpenAI 兼容端点（opencode/tokenrhythm/openai/deepseek/qwen）默认不返回 usage，
    # 必须显式 stream_options.include_usage，最后一个 chunk 才带 token 计数；
    # 否则 stats 中间件读到的 usage_metadata 为空，统计条只显示轮数/步数/耗时而无 token。
    # Anthropic 流式自带 usage，不走此参数。
    stream_usage_kwargs = (
        {"model_kwargs": {"stream_options": {"include_usage": True}}}
        if ModelConfig.streaming
        else {}
    )
    # 推理档位：仅 OpenAI 协议族（openai/minimax/opencode/deepseek）透传顶层
    # reasoning_effort（通用三档 low/medium/high）；qwen/anthropic 走专有参数
    # 体系（enable_thinking/budget_tokens），不注入。
    from noesis.llm.reasoning import REASONING_LEVELS, to_wire_reasoning_effort

    reasoning_kwargs = (
        {"reasoning_effort": to_wire_reasoning_effort(reasoning_effort)}
        if reasoning_effort in REASONING_LEVELS
        and model_type in {"openai", "minimax", "opencode", "deepseek"}
        else {}
    )

    model_map = {
        # openai / minimax 走统一 OpenAI 兼容适配：reasoning 自动探测，
        # 响应有 reasoning_content/reasoning 字段就提取，没有则正常工作。
        "openai": lambda: ChatOpenAICompatible(
            model=model_name,
            temperature=temperature,
            base_url=model_base_url,
            api_key=model_api_key,
            timeout=timeout,
            max_retries=max_retries,
            streaming=ModelConfig.streaming,
            **stream_usage_kwargs,
            **reasoning_kwargs,
            **http_kwargs,
        ),
        "minimax": lambda: ChatOpenAICompatible(
            model=model_name,
            temperature=temperature,
            base_url=model_base_url,
            api_key=model_api_key,
            timeout=timeout,
            max_retries=max_retries,
            streaming=ModelConfig.streaming,
            **stream_usage_kwargs,
            **reasoning_kwargs,
            **http_kwargs,
        ),
        # opencode 与 openai 同为 OpenAI 兼容端点，复用同一适配类；
        # 仅保留 opencode 默认 base_url 与 headers 的便捷默认值。
        "opencode": lambda: ChatOpenAICompatible(
            model=model_name,
            temperature=temperature,
            base_url=model_base_url or _opencode_preset()[0],
            api_key=model_api_key,
            timeout=timeout,
            max_retries=max_retries,
            streaming=ModelConfig.streaming,
            default_headers=_opencode_preset()[1],
            **stream_usage_kwargs,
            **reasoning_kwargs,
            **http_kwargs,
        ),
        # qwen 走 DashScope 专有参数体系（enable_thinking/thinking_budget），
        # 不支持通用 reasoning_effort，本功能不注入
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
            **stream_usage_kwargs,
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
            **stream_usage_kwargs,
            **reasoning_kwargs,
            **http_kwargs,
        ),
        # anthropic 走 thinking/budget_tokens 专有参数体系，不支持通用
        # reasoning_effort，本功能不注入
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


def get_llm(
    purpose: str | None = None,
    *,
    model_id: str | None = None,
    temperature_override: float | None = None,
    reasoning_effort: str | None = None,
):
    from noesis.llm.catalog import resolve_catalog_entry
    from noesis.llm.reasoning import get_request_reasoning_effort
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
        # 自定义模型：id 为复合「slug/model_id」选择器身份，线上名走 wire_name
        model_name = runtime_snapshot.wire_name or runtime_snapshot.id
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
        model_name = entry.id
        temperature_str = str(entry.temperature)
        model_base_url = entry.base_url
    else:
        model_type = ModelConfig.model_type.strip().lower()
        model_name = ModelConfig.model_name
        temperature_str = ModelConfig.model_temperature
        model_base_url = ModelConfig.model_base_url

    model_api_key = runtime_snapshot.api_key if runtime_snapshot is not None else ModelConfig.model_api_key

    # 推理档位：显式参数优先，回退本 Run 的 ContextVar（子 Agent 随
    # create_task 自动继承）；摘要/压缩模型不吃档位。无能力门控——
    # 不支持的端点自行忽略该参数。
    if reasoning_effort is None:
        reasoning_effort = get_request_reasoning_effort()
    if use_summary_model:
        reasoning_effort = None

    if not model_type:
        raise ValueError("MODEL_TYPE environment variable is not set.")

    if not model_api_key:
        raise ValueError("MODEL_API_KEY environment variable is not set.")

    try:
        temperature = float(temperature_str)
    except ValueError:
        raise ValueError(f"Invalid MODEL_TEMPERATURE value: {temperature_str}. Must be a float.")
    if temperature_override is not None:
        temperature = temperature_override

    return build_chat_model(
        model_type=model_type,
        model_name=model_name,
        temperature=temperature,
        model_base_url=model_base_url,
        model_api_key=model_api_key,
        provider_max_retries=(
            int(ModelConfig.max_retries) if purpose == "summarization" else 0
        ),
        reasoning_effort=reasoning_effort,
    )
