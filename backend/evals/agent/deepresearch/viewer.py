"""从 raw/ 原始流生成离线 HTML 轨迹查看器。

用法（backend/ 下）:
    uv run python -m evals.agent.deepresearch.viewer --tag final-20p-v2
    uv run python -m evals.agent.deepresearch.viewer --tag final-20p-v2 --task 20

产物: results/<tag>/viewer.html（单文件，浏览器直接打开，无需服务端）
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path

RACE_ROOT = Path(__file__).resolve().parent
RESULTS_ROOT = RACE_ROOT / "results"

# HTML 模板：内嵌 CSS + JS，纯客户端渲染
_TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<title>DeepResearch 轨迹查看器 — {tag}</title>
<style>
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
body {{ font-family: -apple-system, "SF Mono", "Helvetica Neue", sans-serif; background: #0d1117; color: #e6edf3; }}
.sidebar {{ position: fixed; left: 0; top: 0; bottom: 0; width: 220px; background: #161b22; border-right: 1px solid #30363d; overflow-y: auto; padding: 12px; }}
.sidebar h2 {{ font-size: 14px; color: #58a6ff; margin-bottom: 12px; }}
.task-btn {{ display: block; width: 100%; padding: 8px 10px; margin-bottom: 4px; background: transparent; border: 1px solid transparent; border-radius: 6px; color: #e6edf3; cursor: pointer; text-align: left; font-size: 13px; }}
.task-btn:hover {{ background: #21262d; }}
.task-btn.active {{ background: #1f6feb33; border-color: #1f6feb; }}
.task-btn .score {{ float: right; color: #7ee787; font-size: 12px; }}
.main {{ margin-left: 220px; padding: 20px; max-width: 900px; }}
.event {{ margin-bottom: 6px; border-radius: 6px; padding: 8px 12px; font-size: 13px; line-height: 1.5; }}
.evt-llm {{ background: #1c2128; border-left: 3px solid #58a6ff; }}
.evt-tool {{ background: #161b22; border-left: 3px solid #d29922; }}
.evt-tool .tool-name {{ color: #d29922; font-weight: 600; }}
.tag {{ display: inline-block; padding: 1px 6px; border-radius: 3px; font-size: 11px; margin-right: 6px; }}
.tag-llm {{ background: #58a6ff22; color: #58a6ff; }}
.tag-tool {{ background: #d2992222; color: #d29922; }}
pre {{ white-space: pre-wrap; word-break: break-all; font-size: 12px; font-family: inherit; }}
details summary {{ cursor: pointer; color: #8b949e; font-size: 12px; }}
details pre {{ margin-top: 6px; padding: 8px; background: #0d1117; border-radius: 4px; }}
.header {{ margin-bottom: 20px; padding: 16px; background: #161b22; border-radius: 8px; }}
.header h1 {{ font-size: 18px; color: #58a6ff; }}
.header .meta {{ color: #8b949e; font-size: 12px; margin-top: 6px; }}
.stats {{ display: flex; gap: 16px; margin-top: 10px; }}
.stat {{ padding: 8px 14px; background: #0d1117; border-radius: 6px; text-align: center; }}
.stat .val {{ font-size: 20px; font-weight: 700; color: #7ee787; }}
.stat .lbl {{ font-size: 11px; color: #8b949e; }}
.search {{ width: 100%; padding: 6px 10px; margin-bottom: 10px; background: #0d1117; border: 1px solid #30363d; border-radius: 6px; color: #e6edf3; font-size: 13px; }}
</style>
</head>
<body>
<div class="sidebar">
<h2>题目列表</h2>
<input class="search" placeholder="搜索 id 或主题…" onkeyup="filterTasks(this.value)">
<div id="taskList"></div>
</div>
<div class="main" id="main"></div>
<script>
const DATA = {data};

function fmt(n) {{ return n ? n.toLocaleString() : '-'; }}

function renderTaskList(filter) {{
  const el = document.getElementById('taskList');
  el.innerHTML = '';
  for (const t of DATA.tasks) {{
    if (filter && !String(t.id).includes(filter) && !t.topic.toLowerCase().includes(filter.toLowerCase())) continue;
    const btn = document.createElement('button');
    btn.className = 'task-btn' + (DATA.tasks.indexOf(t) === DATA.selected ? ' active' : '');
    btn.innerHTML = `id=${{t.id}} ${{t.topic.slice(0,12)}}… <span class="score">${{t.score !== null ? (t.score*100).toFixed(1)+'%' : '—'}}</span>`;
    btn.onclick = () => {{ DATA.selected = DATA.tasks.indexOf(t); render(); }};
    el.appendChild(btn);
  }}
}}

function filterTasks(v) {{ renderTaskList(v); }}

function render() {{
  renderTaskList('');
  const t = DATA.tasks[DATA.selected];
  const main = document.getElementById('main');

  let html = `<div class="header">
    <h1>id=${{t.id}} — ${{t.topic}}</h1>
    <div class="meta">模型: ${{t.model}} | 会话: ${{t.session_id}} | 耗时: ${{t.elapsed}}s | ${{t.error || '无错误'}}</div>
    <div class="stats">
      <div class="stat"><div class="val">${{t.score !== null ? (t.score*100).toFixed(1)+'%' : '—'}}</div><div class="lbl">RACE</div></div>
      <div class="stat"><div class="val">${{fmt(t.input_tokens)}}</div><div class="lbl">input tok</div></div>
      <div class="stat"><div class="val">${{fmt(t.output_tokens)}}</div><div class="lbl">output tok</div></div>
      <div class="stat"><div class="val">${{t.article_chars}}</div><div class="lbl">报告字数</div></div>
      <div class="stat"><div class="val">${{t.tool_count}}</div><div class="lbl">工具调用</div></div>
    </div>
  </div>`;

  for (const ev of t.events) {{
    if (ev.type === 'llm') {{
      html += `<div class="event evt-llm"><span class="tag tag-llm">LLM</span>${{ev.text}}</div>`;
    }} else if (ev.type === 'tool') {{
      html += `<div class="event evt-tool"><span class="tag tag-tool">工具</span><span class="tool-name">${{ev.name}}</span>`;
      if (ev.input) html += `<details><summary>入参</summary><pre>${{ev.input}}</pre></details>`;
      if (ev.output) html += `<details><summary>输出</summary><pre>${{ev.output}}</pre></details>`;
      html += `</div>`;
    }}
  }}

  main.innerHTML = html;
}}

render();
</script>
</body>
</html>"""


def build_viewer(tag: str, task_filter: int | None = None) -> Path:
    out_dir = RESULTS_ROOT / tag
    raw_dir = out_dir / "raw"
    if not raw_dir.is_dir():
        print(f"无原始流目录: {raw_dir}", file=__import__("sys").stderr)
        raise SystemExit(2)

    # 载入 articles + scores + tasks
    articles = {}
    articles_path = out_dir / "articles.jsonl"
    if articles_path.is_file():
        for line in articles_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                articles[r["id"]] = r

    scores = {}
    scores_path = out_dir / "scores.jsonl"
    if scores_path.is_file():
        for line in scores_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                scores[r["id"]] = r

    tasks = []
    for raw_file in sorted(raw_dir.glob("*.jsonl")):
        try:
            task_id = int(raw_file.stem)
        except ValueError:
            continue
        if task_filter and task_id != task_filter:
            continue

        events = []
        session_id, model = "", ""
        input_tokens = output_tokens = 0
        current_text = []  # 当前文本段的流式增量
        tool_count = 0
        # 工具事件按 tool_call_id 配对（output 帧无 name）
        pending_tools: dict[str, dict] = {}

        for line in raw_file.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue

            # SSE 帧（event:/data: 行，生产同源词表）
            if stripped.startswith(("event:", "data:")):
                if not stripped.startswith("data:"):
                    continue
                try:
                    obj = json.loads(stripped[len("data:"):].strip())
                except ValueError:
                    continue  # [DONE] 等非 JSON 帧
                if not isinstance(obj, dict):
                    continue
                dtype = str(obj.get("type") or "")
                if dtype == "text-delta":
                    current_text.append(str(obj.get("text_delta") or ""))
                elif dtype == "text-end":
                    text = "".join(current_text).strip()
                    if text:
                        events.append({"type": "llm", "text": text[:2000]})
                    current_text = []
                elif dtype == "tool-input-available":
                    tool_count += 1
                    tid = str(obj.get("tool_call_id") or "")
                    inp = json.dumps(obj.get("input"), ensure_ascii=False)
                    ev = {"type": "tool", "name": obj.get("name", "?"), "input": inp[:500]}
                    events.append(ev)
                    if tid:
                        pending_tools[tid] = ev
                elif dtype == "tool-output-available":
                    ev = pending_tools.pop(str(obj.get("tool_call_id") or ""), None)
                    if ev is not None:
                        ev["output"] = str(obj.get("output") or "")[:3000]
                elif dtype == "stats-update":
                    # 累计快照：取最新值。input_tokens 含 cache 读（数值巨大），
                    # 计费口径看 uncached_input_tokens（含子 Agent 调用）
                    input_tokens = int(obj.get("uncached_input_tokens")
                                       or obj.get("input_tokens") or 0)
                    output_tokens = int(obj.get("output_tokens") or 0)
                continue

            # 纯 JSON 行：__tw_init__（会话/模型标识）
            try:
                obj = json.loads(stripped)
            except ValueError:
                continue
            if obj.get("type") == "__tw_init__":
                session_id = obj.get("session_id", "")
                model = obj.get("model", "")

        article = articles.get(task_id, {})
        score = scores.get(task_id, {})
        # 转义 HTML
        for e in events:
            for k in ("text", "input", "output"):
                if k in e:
                    e[k] = html.escape(e[k])

        tasks.append({
            "id": task_id,
            "topic": article.get("topic", "?"),
            "session_id": session_id,
            "model": model,
            "elapsed": article.get("elapsed_seconds", 0),
            "error": article.get("error"),
            "score": score.get("overall_score"),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "article_chars": len(article.get("article") or ""),
            "tool_count": tool_count,
            "events": events,
        })

    if not tasks:
        print("无可渲染的题目", file=__import__("sys").stderr)
        raise SystemExit(2)

    data = {"tasks": tasks, "selected": 0}
    html_content = _TEMPLATE.format(tag=tag, data=json.dumps(data, ensure_ascii=False))
    out_path = out_dir / "viewer.html"
    out_path.write_text(html_content, encoding="utf-8")
    print(f"轨迹查看器: {out_path}")
    print(f"包含 {len(tasks)} 题，浏览器直接打开即可")
    return out_path


def main() -> int:
    p = argparse.ArgumentParser(description="从 raw/ 生成离线 HTML 轨迹查看器")
    p.add_argument("--tag", required=True, help="结果目录名（如 final-20p-v2）")
    p.add_argument("--task", type=int, default=None, help="只渲染指定题号")
    args = p.parse_args()
    build_viewer(args.tag, args.task)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
