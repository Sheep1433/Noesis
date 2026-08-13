# Web 搜索引用全量丢失（偶发）

## 状态

🆕 待复现定位｜已加临时 debug 日志，等待下次触发时抓取 provider 原始返回。

## 现象

某轮问答中，`web_search` 检索到多条结果，但前端**一次引用溯源都没有**（无 `CitationSources` 卡片，正文也无 `[1]`/`[2]`）。后端日志连续 8 行：

```
noesis.agents.tools.web_search_tool:web_search:63 - 忽略不可引用的 Web 搜索结果: invalid web evidence URL
```

换 query 重跑后未复现——属 provider 侧偶发脏数据，非稳定 bug。

## 根因链路

引用溯源是两段式，本次卡在第 1 段的结构化来源校验：

1. **结构化来源**（前端引用卡片）：`web_search` / `search_knowledge_base` 结果经 `message_builder.register_retrieval_results` 登记为 `RetrievalPart`，通过 `retrieval-results-available` SSE 事件下发，前端 `chat.vue:83` 聚合渲染。要求每条结果 `citable=True` 且通过 `EvidenceEnvelope` 校验。
2. **正文内联引用**（`[1]`/`[2]` + `### 参考资料`）：由 `CITATION_EXTENSION` prompt 驱动，但前提是模型实际收到了来源。

本次 web 搜索结果在第 1 段就被清空，模型最终收到 `{"results": [], "total_results": 0}`。`CITATION_EXTENSION` 明确写了「工具结果没有提供来源时，不添加引用并说明依据不足」——模型按规矩没给引用，行为符合 prompt，问题在上游检索结果被清空，不在模型。

### 清空位置

`web_search_tool.py` `_normalize_web_result` 调 `_canonical_url`（`:30`），该校验要求：

- scheme 必须是 `http`/`https`
- 必须有 hostname
- 不能带 username/password

不满足即抛 `ValueError`，被 `web_search:63` 的 `except (TypeError, ValueError)` 吞掉，该条结果直接丢弃。8 条全部未过 → `registered == []` → 模型无来源。

### provider 取值 key

- `tavily.py:31`：`item.get("url")`
- `ddg.py:44`：`row.get("href") or row.get("link")`

若 provider 这批结果 URL 字段为空或字段名对不上（DDG 的 `mojeek`/`yandex` backend 字段名可能不同），`_canonical_url("")` → scheme 为空 → 直接判废。这是最可能的触发原因。

## 与模型无关

`_canonical_url` 过滤发生在模型调用之前，任何模型跑那一轮拿到的都是空结果。日志中未记录当时所用模型，且换模型无意义——这是工具层问题。

## 临时 debug 日志（待触发后删除）

两处临时日志，复现定位后须清除：

### 1. `backend/packages/noesis-core/src/noesis/agents/tools/web_search_tool.py`

`_normalize_web_result` 入口，打印每条原始结果的 `url`/`title`/`keys`：

```python
def _normalize_web_result(item: dict) -> dict:
    # TEMP-DEBUG: 排查 web_search 结果被全量丢弃的原因
    try:
        _raw_url = str(item.get("url") or "")
        logger.info(
            "[TEMP-DEBUG] web_search 原始结果 url={!r} title={!r} keys={}",
            _raw_url,
            str(item.get("title") or "")[:80],
            sorted(item.keys()),
        )
    except Exception as _dbg:
        logger.info("[TEMP-DEBUG] 打印 web_search 原始结果失败: {}", _dbg)
    url = _canonical_url(str(item.get("url") or ""))
    ...
```

### 2. `backend/server/api/chat_api.py` `stop_run`

打印 stop 请求的真实异常类型与 message（前端「停止请求失败」是 catch-all 文案，"重新登录"为误导，真实抛错在 `parseAuthJson` 的 `json.code !== 200`）：

```python
@chat_router.post("/runs/{run_id}/stop", summary="停止 Agent 任务")
async def stop_run(...):
    try:
        snapshot = await RunService.stop(run_id, str(current_user.user_id), db)
        logger.info("[TEMP-DEBUG] stop_run 成功 ...")
        return ResponseUtil.success(...)
    except Exception as e:
        logger.warning("[TEMP-DEBUG] stop_run 失败 run_id={} type={} msg={}", ...)
        raise
```

## 待验证

下次复现时抓取以下日志确认根因：

1. `[TEMP-DEBUG] web_search 原始结果 url=...` —— 看 8 条 URL 是否空串；若非空但 `keys` 里 URL 叫别的名字，则是 provider 字段名问题。
2. `[TEMP-DEBUG] stop_run 失败 type=...` —— 看 stop 请求真实异常（403 CSRF / 404 任务不存在 / 500 / 网络）。

## 可能的修复方向（待确认根因后定）

- **provider 字段名补全**：`ddg.py:44` 加 `url`，`tavily.py:31` 兼容其它 key。
- **全量丢弃时不静默**：当 `registered` 为空但原始 `results` 非空时，返回明确信号让模型至少说明"检索到结果但来源不可引用"，而非空结果。

## 附带：`<unk>` 连发

同一时段用 Nemotron 模型出现正文连续输出 `<unk>`。Noesis 的 `factory.py` 流式只透传模型服务端返回的 `delta.content`，不做 decode/token 改写，故 `<unk>` 来自模型服务端（tokenizer/chat-template 不匹配）。换模型可消失，与 Noesis 代码无关，此处仅记录关联现象，不单独排查。
