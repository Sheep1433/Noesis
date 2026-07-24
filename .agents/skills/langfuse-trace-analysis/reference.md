# Langfuse Trace Analysis — Noesis 参考

按需阅读；默认先跟 `SKILL.md` 主流程。

## 访问

| 项 | 值 |
|----|-----|
| SSH | `zzqroot`（`~/.ssh/config` → 腾讯云） |
| Langfuse UI | 隧道后 `http://127.0.0.1:8888`；公网常不开放 8888 |
| 隧道 | `ssh -fN -L 8888:127.0.0.1:8888 zzqroot` |
| 部署目录 | `/root/zzq/code/noesis/deploy` |
| Backend 容器 | `noesis-backend-1` |
| Postgres 容器 | `noesis-postgres-1`（用户/库默认 `noesis`） |
| Compose env | `deploy/.env.docker`（含 `LANGFUSE_*`、`POSTGRES_PASSWORD`） |

重启 backend 时需同时提供：

- `--env-file .env.docker`
- `NOESIS_HOST_DATA_DIR` / `NOESIS_HOST_SKILLS_DIR`（宿主机绝对路径）

## 项目

| 名称 | 用途 |
|------|------|
| **Noesis-prod** | 生产应对；URL 常含 `project_db07c93545e2cc0e` |
| **Noesis-local-dev** | 历史误配密钥上报处；分析旧 trace 仍可能在此 |

以 **当前 backend 容器内 PK/SK** 调 `/api/public/projects` 确认绑定项目。

## API（Basic Auth = public_key:secret_key）

```bash
# 从容器取密钥（勿打印完整值到日志仓库）
PK=$(ssh zzqroot 'docker exec noesis-backend-1 printenv LANGFUSE_PUBLIC_KEY')
SK=$(ssh zzqroot 'docker exec noesis-backend-1 printenv LANGFUSE_SECRET_KEY')

curl -sS -u "$PK:$SK" "http://127.0.0.1:8888/api/public/projects"
curl -sS -u "$PK:$SK" "http://127.0.0.1:8888/api/public/traces?sessionId=<SID>&limit=20"
curl -sS -u "$PK:$SK" "http://127.0.0.1:8888/api/public/traces/<TRACE_ID>" -o /tmp/lf_trace.json
```

本地隧道已开时，上述 `curl` 在本机执行即可。

## DB 查询示例

```bash
ssh zzqroot 'cd /root/zzq/code/noesis/deploy && . ./.env.docker && \
  docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" noesis-postgres-1 \
  psql -U noesis -d noesis -c \
  "SELECT id, role, status, to_timestamp(created_at/1000.0) AS created, left(content::text,200) \
   FROM t_chat_message WHERE session_id = '\''<SID>'\'' ORDER BY created_at;"'
```

单条全文：

```bash
... -Atc "SELECT content::text FROM t_chat_message WHERE id='<MSG_ID>';"
```

表：`t_chat_session`（`id`/`title`/`extra`）、`t_chat_message`（`session_id`/`role`/`content`/`extra`/`status`/`created_at` ms）。

## Langfuse vs DB

| 需要 | 优先源 |
|------|--------|
| LLM 完整 prompt、中间件 span、cancel 堆栈感信息 | Langfuse |
| 用户可见 parts、`partial`、会话 token | DB |
| 工具 I/O | 两边都有；失败文本两边都要扫 |
| 跨轮因果（调研中断 → 继续 → 生成 Word） | session 级 traces + DB 消息序列 |

## 已知陷阱

1. **DB `tool.status=success` 但 output 含 Command failed** — 以 output / exit code 为准  
2. **单条 peek 不够** — pip/node 常在后续「生成 Word」轮  
3. **`cd foo && cmd` → `failed to run command 'cd'`** — 疑似沙箱/timeout 包装解析问题，记为 env/runtime  
4. **web_search `provider=ddg` + `mojeek,yandex`** — 中文政务/统计召回弱；噪声可跨 query 重复  
5. **宽泛堆词 query** 加重偏题，但弱后端单独也能把具体 query 打空  
6. **改密钥后历史 trace 不会搬家** — 新旧项目都可能要查

## 扫描脚本

相对 Noesis 仓库根：

```bash
python3 .agents/skills/langfuse-trace-analysis/scripts/scan_trace.py /tmp/lf_trace.json
python3 .agents/skills/langfuse-trace-analysis/scripts/scan_trace.py /tmp/db_msg.json --db-parts
```
