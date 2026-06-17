import httpx
from langchain_openai import ChatOpenAI
from langchain_deepseek import ChatDeepSeek
from langchain_qwq import ChatQwen
from config.env import ModelConfig

_OPENCODE_DEFAULT_BASE_URL = "https://opencode.ai/zen/v1"
_OPENCODE_DEFAULT_HEADERS = {
    "HTTP-Referer": "https://opencode.ai/",
    "X-Title": "opencode",
}


def _llm_http_timeout() -> httpx.Timeout:
    """读超时 = 无响应间隔上限；连接超时单独限制避免内网挂死过久。"""
    read_sec = float(ModelConfig.request_timeout)
    return httpx.Timeout(connect=10.0, read=read_sec, write=read_sec, pool=10.0)


def _build_chat_model(
    *,
    model_type: str,
    model_name: str,
    temperature: float,
    model_base_url: str,
    model_api_key: str,
):
    timeout = _llm_http_timeout()
    max_retries = int(ModelConfig.max_retries)

    model_map = {
        "openai": lambda: ChatOpenAI(
            model=model_name,
            temperature=temperature,
            base_url=model_base_url,
            api_key=model_api_key,
            timeout=timeout,
            max_retries=max_retries,
        ),
        "minimax": lambda: ChatOpenAI(
            model=model_name,
            temperature=temperature,
            base_url=model_base_url,
            api_key=model_api_key,
            timeout=timeout,
            max_retries=max_retries,
        ),
        "opencode": lambda: ChatOpenAI(
            model=model_name,
            temperature=temperature,
            base_url=model_base_url or _OPENCODE_DEFAULT_BASE_URL,
            api_key=model_api_key,
            timeout=timeout,
            max_retries=max_retries,
            default_headers=_OPENCODE_DEFAULT_HEADERS,
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
        ),
        "deepseek": lambda: ChatDeepSeek(
            model=model_name,
            temperature=temperature,
            base_url=model_base_url,
            api_key=model_api_key,
            timeout=timeout,
            max_retries=max_retries,
        ),
    }

    if model_type in model_map:
        return model_map[model_type]()
    raise ValueError(
        f"Unsupported MODEL_TYPE: {model_type}. "
        f"Supported types: {', '.join(model_map.keys())}"
    )


def get_llm(purpose: str | None = None):
    use_summary_model = purpose == "summarization" and bool(
        ModelConfig.summarization_model_name.strip()
    )

    model_type = ModelConfig.model_type.strip().lower()
    model_api_key = ModelConfig.model_api_key
    model_base_url = ModelConfig.model_base_url

    if use_summary_model:
        model_name = ModelConfig.summarization_model_name.strip()
        temperature_str = str(ModelConfig.summarization_model_temperature)
    else:
        model_name = ModelConfig.model_name
        temperature_str = ModelConfig.model_temperature

    if not model_type:
        raise ValueError("MODEL_TYPE environment variable is not set.")

    if not model_api_key:
        raise ValueError("MODEL_API_KEY environment variable is not set.")

    try:
        temperature = float(temperature_str)
    except ValueError:
        raise ValueError(f"Invalid MODEL_TEMPERATURE value: {temperature_str}. Must be a float.")

    return _build_chat_model(
        model_type=model_type,
        model_name=model_name,
        temperature=temperature,
        model_base_url=model_base_url,
        model_api_key=model_api_key,
    )
