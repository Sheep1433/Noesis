#!/usr/bin/env bash
# 发现并验证免费 LLM 模型（OpenCode Zen / models.dev 目录）。
#
# 核心：公共 API Key `public` 只在 OpenCode Zen 与 Kilo Gateway 上放行免费模型，
# 其它 provider 各自需要真实密钥，用 public 测它们只会全 ERR（噪声）。
# 所以默认只测已知网关；--all 才遍历全部 provider（慢，多数会失败）。
#
# 用法:
#   ./find_free_models.sh discover              # 列出已知网关的免费活跃 chat 模型（默认）
#   ./find_free_models.sh discover --all        # 列出全部 provider 的免费活跃 chat 模型
#   ./find_free_models.sh test                  # 实测已知网关模型（默认）
#   ./find_free_models.sh test --all            # 实测全部 provider（慢，多数 ERR）
#   ./find_free_models.sh                       # 等同 test
#
# 内部子命令（供并行调用，不直接用）:
#   ./find_free_models.sh _one <pid> <mid> <api>
set -uo pipefail

CATALOG="https://models.dev/api.json"
HTTP_REFERER="https://opencode.ai/"
X_TITLE="opencode"
TIMEOUT=15
AUTH_KEY="public"

# 已知放行 public key 的网关 provider id
GATEWAY_IDS="opencode kilo"

# 列出模型: pid\tmid\tapi\tctx
list_models() {
  local only_gateways="$1"
  curl -s "$CATALOG" | python3 -c '
import json, sys
only_gateways = sys.argv[1] == "1"
gateways = set(sys.argv[2].split())
data = json.load(sys.stdin)
out = []
for pid, p in data.items():
    if only_gateways and pid not in gateways:
        continue
    api = p.get("api", "")
    if not api:
        continue
    for mid, m in p.get("models", {}).items():
        cost = m.get("cost", {})
        if cost.get("input", 0) == 0 and m.get("status", "active") == "active":
            ctx = m.get("limit", {}).get("context", 0)
            if not isinstance(ctx, int) or ctx <= 0:
                continue
            out.append((pid, mid, api, ctx))
out.sort()
for pid, mid, api, ctx in out:
    print(f"{pid}\t{mid}\t{api}\t{ctx}")
' "$only_gateways" "$GATEWAY_IDS"
}

discover() {
  local all="${1:-}"
  local out
  out=$(list_models "$([ -z "$all" ] && echo 1 || echo 0)")
  if [ -z "$out" ]; then
    echo "(无免费活跃 chat 模型)"
    return
  fi
  printf '%s\n' "$out" | column -t -s $'\t'
}

# 测单个模型，输出一行 TSV: 状态<TAB>模型<TAB>实际路由<TAB>端点<TAB>输出/错误
# 入参: $1=pid $2=mid $3=api
_one() {
  local pid="$1" mid="$2" api="$3"
  local body
  body=$(printf '{"model":"%s","messages":[{"role":"user","content":"1+1=?"}],"max_tokens":20}' "$mid")
  local resp
  resp=$(curl -s --max-time "$TIMEOUT" \
    "${api%/}/chat/completions" \
    -H "Authorization: Bearer ${AUTH_KEY}" \
    -H "HTTP-Referer: ${HTTP_REFERER}" \
    -H "X-Title: ${X_TITLE}" \
    -H "Content-Type: application/json" \
    -d "$body" 2>/dev/null)

  python3 -c '
import json, sys
resp_raw, pid, mid, api_base = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
try:
    resp = json.loads(resp_raw)
except Exception:
    print(f"?\t{pid}/{mid}\t-\t{api_base}\t非 JSON: {resp_raw[:60]}")
    sys.exit(0)
if "choices" in resp:
    msg = resp["choices"][0].get("message", {})
    content = (msg.get("content") or "").strip()
    if not content and msg.get("reasoning"):
        content = "(reasoning)"
    content = content.replace("\t", " ").replace("\n", " ")[:80]
    model_used = resp.get("model", "?").replace("\t", " ")
    print(f"OK\t{pid}/{mid}\t{model_used}\t{api_base}\t{content}")
else:
    err = resp.get("error", {})
    if isinstance(err, dict):
        err = err.get("message", str(err))
    err = str(err).replace("\t", " ").replace("\n", " ")[:80]
    print(f"ERR\t{pid}/{mid}\t-\t{api_base}\t{err}")
' "$resp" "$pid" "$mid" "$api"
}

run_tests() {
  local all="${1:-}"
  echo "拉取目录并筛选免费活跃 chat 模型..."
  local candidates
  candidates=$(list_models "$([ -z "$all" ] && echo 1 || echo 0)")
  if [ -z "$candidates" ]; then
    echo "(无候选)"
    return
  fi
  local total
  total=$(printf '%s\n' "$candidates" | grep -c .)
  echo "共 $total 个候选，并行实测（每个超时 ${TIMEOUT}s，并发 8）..."
  echo
  printf '状态\t模型\t实际路由\t端点\t输出/错误\n'

  # xargs -L 1: 每行按空白拆成多个 arg（pid/mid/api 均不含空白，安全）
  local self
  self="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
  printf '%s\n' "$candidates" | awk -F'\t' '{print $1"\t"$2"\t"$3}' | \
    xargs -P 8 -L 1 "$self" _one | column -t -s $'\t' 2>/dev/null || \
    printf '%s\n' "$candidates" | awk -F'\t' '{print $1"\t"$2"\t"$3}' | \
    while IFS=$'\t' read -r p m a; do "$self" _one "$p" "$m" "$a"; done
}

main() {
  case "${1:-all}" in
    discover)
      if [ "${2:-}" = "--all" ]; then discover "--all"
      else discover; fi
      ;;
    test)
      if [ "${2:-}" = "--all" ]; then run_tests "--all"
      else run_tests; fi
      ;;
    _one)
      _one "$2" "$3" "$4"
      ;;
    all|"")
      run_tests
      ;;
    *) echo "用法: $0 [discover [--all] | test [--all]]"; exit 1 ;;
  esac
}

# 仅直接执行时跑 main，source 时不跑
if [ "${BASH_SOURCE[0]:-$0}" = "$0" ]; then
  main "$@"
fi
