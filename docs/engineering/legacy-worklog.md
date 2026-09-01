# 旧工作日志（迁移自 docs/NOTES.md 未带决策日期的条目）

> 2026-09-01 自 docs/NOTES.md 机械拆分，内容零改写。这些是工程操作与修复记录，不是决策。

## DeepDoc Vendor 修改清单

| 路径 | 原因 | 同步策略 |
|------|------|----------|
| `backend/kb/deepdoc/parser/resume/`（整目录） | Noesis 知识库仅 PDF/Office/Markdown 入库，不用 HR 简历结构化解析；该目录含 ~5.5 万行词典 JSON/CSV，占 git 体积 | **drop**（合并 upstream 时不带回） |

## 知识库 Agent 工具（2026-07-01）

- **三工具**：`list_knowledge_bases`（发现）、`search_knowledge_base`（片段 hybrid，可传 `collection_names`）、`get_knowledge_document`（整篇补全，80k 字符截断）。
- **检索范围**：工具入参 > 会话 `extra.kb_collections` > 全部可用库；多库 **ThreadPoolExecutor 并行**，每库 `final_top_k=global_limit` 后全局 merge（不再 `ceil(limit/N)` 预截断）。
- **前端**：COMMON_QA 输入区 `KbScopeSelector` 多选写回 `extra.kb_collections`；流式 `extra.kb_collections` 同步会话。
- **检索耗时日志**：`KbRetrievalService.search` → `[KbRetrievalService] search`（recall/parse/rerank/post/total ms）；Agent 跨库 → `[KbSearchTool] search_knowledge_base`（resolve/parallel/merge/total ms）。`grep` 后端 `.noesis/logs` 或控制台即可。

## 知识库上传与 Rerank 配置（2026-07-01）

- **上传暂存**：`POST .../upload` 写入 `.noesis/kb_uploads/{collection}/{file_hash}_{原名}`，解析后删除；Qdrant 分片 `file_name` 经 `source_file_name` 显式传入，不再用 `basename(tmp)`。
- **Rerank 密钥**：`ModelSettings.rerank_model_api_key` 在 `noesis/config/env.py` 中回退 `embedding_model_api_key`；`RERANK_MODEL_API_KEY` 仅作可选覆盖，prod 不必单独配置。

## 动态切换对话模型（2026-07-02）

- **参考**：LangGraph Runtime / Context 思路——图定义不变，运行时按 `model_id` 选择 LLM 实例；Noesis 采用「配置目录 + 会话 sticky + 请求 extra」而非 middleware 拦截。
- **配置**：`config.yaml` → `model.catalog[]`（id/label/type/name/temperature/base_url）+ `default_catalog_id`；密钥仍用 `.env` `MODEL_API_KEY`。
- **后端**：`get_llm(model_id=...)` → `llm/catalog.py` 解析；`QaService._resolve_model_for_query` 写回 `session.extra.model_id`；Agent `create_noesis_agent(model_id=...)`；assistant 落库 `extra.model`。
- **API**：`GET /api/models`；流式 `extra.model_id`。
- **前端**：输入区 `ModelSelector`（非 TEST_CASE_QA）；切换后 `ensureSession` 持久化。

- **目录契约增补（2026-08-15）**：catalog 已收敛为 `id`（provider 模型全名）+ `label`（展示名）+ `model_type` + `context_window`；同步修改 Pydantic `ModelCatalogItem` 和前端 `ChatModelOption`。只改配置而不改 schema 会让 `/api/models` 继续要求已删除的 `model_name`/`limit`，最终在序列化阶段失败。

## OpenCode Zen 免费模型目录（2026-07-02）

- **发现**：`https://models.dev/api.json` 中 `opencode` provider、`cost.input==0 && status==active`；网关 `https://opencode.ai/zen/v1`，`.env` `MODEL_API_KEY=public`。
- **当前可用（实测）**：`deepseek-v4-flash-free`、`big-pickle`、`mimo-v2.5-free`、`nemotron-3-ultra-free`、`north-mini-code-free`；`deepseek-reasoner` **不支持**于 Zen 免费网关。
- **配置**：`config.yaml` → `model.catalog[]` 逐项列出；界面仅展示 catalog 内条目，未写入 catalog 的模型不会出现。

## KB/Web 引用溯源（2026-08-01）

- **变更**：`openspec/changes/add-kb-citation-sources/`。
- **回答协议**：COMMON_QA 与 SuperAgent 共用 Prompt citation；Web 使用 Markdown 原始链接，KB 使用编号和文末参考资料。
- **平台边界**：正文继续走普通 `text-delta`；独立 retrieval part 保存本轮来源，用于折叠展示和刷新恢复，不推断 cited 子集。
- **明确删除**：structured answer、虚拟 Tool、typed annotation、citation resolve API、前端 offset marker 和旧兼容分支。
- **验证**：保留真实模型 Web citation 集成测试，同时检查实时流和终态消息中的 Markdown 来源。
- **Provider 能力门禁（2026-08-03）**：MiMo 的 `function_calling`、`json_schema`、`json_mode` 在固定对照用例中均稳定返回 500；这不是偶发网络错误，也不是工具数量导致。移除 MiMo 的结构化引用白名单后，同一模型和工具集合可正常产生普通工具调用。以后不能用模型目录或单次成功请求声明结构化能力，必须在目标 provider + 实际工具集合上做能力门禁；门禁失败时关闭结构化引用，不阻断普通对话。

- **引用匹配增补（2026-08-15）**：检索记录的 Web URL 可能带 tracking query，而模型在参考资料中输出的 URL 不带 query；用完整 URL 严格比较会导致引用编号无法变成上标。匹配键应规范化为 `origin + pathname`（忽略 query/hash），展示链接仍保留经过安全校验的完整 URL，并用真实 tracking URL 回归测试覆盖。

## 沙箱 execute 虚拟路径统一（2026-07-02）

- **问题**：`read_file` 走 Composite 路由成功，`execute("cat /research/...")` 在容器内找不到；`AioSandboxBackend` 仅 rewrite custom skills。
- **根因**：deepagents `CompositeBackend.execute()` **只委托 default workspace** backend，extensions/custom/memory 的 `PrefixBackend.execute()` 不会被调用。
- **方案**：`path_rewrite.py` 新增 `PathRewriteContext` + token 级 `rewrite_virtual_paths_in_command`（`shlex` 分词，Tier1 `/research/`、`/skills/extensions|custom/`、`/memory/*.md` + Tier2 workspace 根）；**仅** workspace `PrefixBackend.execute()` 调用；移除 `AioSandboxBackend._rewrite_custom_skill_paths_in_command`。
- **协调**：`/memory/AGENTS.md` execute → `/workspace/AGENTS.md`（与 `add-super-agent-user-memory` D3 一致）；本 change **先于** super-agent 归档。
- **已知限制**：`pwd` 仍输出物理路径；`cat /workspace/AGENTS.md` bypass 不拦截；heredoc/`$()` 内路径可能漏 rewrite。
- **OpenSpec**：`openspec/changes/archive/2026-07-02-unify-sandbox-virtual-paths/`。

## KB Markdown 分块修复（2026-07-03）

- **现象**：`.md` 入库仅 2 片（~20k 字医疗教材）。
- **根因（两层，均在 vendor 外）**：① `deepdoc_service._parse_markdown` 曾整文件 1 block（非 upstream 行为）；② `deepdoc_adapter._chunk_blocks` 对单 block 超 `chunk_size` 未滑窗，几乎整篇 emit。
- **修复**：`.md` 解析改为调用 vendored `RAGFlowMarkdownParser` + `MarkdownElementExtractor`（与 RAGFlow `naive.py` 一致，**不改** `kb/deepdoc/`）；adapter 对超长 block 用 `_fixed_window_chunks` 兜底。
- **验证**：`test_kb_deepdoc.py::test_chunk_medical_markdown_produces_many_shards`（seed 文档 ≥20 片、每片 ≤500 字）。

## web_search DDG 回退引擎白名单（2026-07-03）

- **现象**：无 `TAVILY_API_KEY` 时 `web_search` 常耗时 30/60/90/120s（整数倍 `fetch_timeout_seconds`）。
- **根因**：`ddgs` 库 `backend=auto` 分批并行试 9 个引擎；国内多数源（wikipedia、duckduckgo、google、brave、yahoo 等）超时，每批白等满 30s，仅 mojeek/yandex 可用。
- **修复**：`web_tools.ddg_backends` 默认 `mojeek,yandex`，`ddg.search_with_ddg` 显式传 `backend=`，不再 `auto` 轮询；可用 `WEB_DDG_BACKENDS` 覆盖（海外可设 `duckduckgo,brave`）。
- **仍建议**：生产配置 `TAVILY_API_KEY` 作为主搜索源。

## AIO 沙箱 write_file 写入 Base64 乱码（2026-07-03）

- **现象**：智能体 `write_file` 落盘 `.md` / `.json` 为 `IyDnoJTnqbb...` 一类 ASCII，非 UTF-8 中文。
- **根因**：`deepagents.BaseSandbox.write()` → `upload_files([(path, content.encode("utf-8"))])` 传 **bytes**；`AioSandboxBackend.upload_files` 原样交给 `agent_sandbox.file.write_file(content=bytes)`，而 SDK 签名要求 **str**（`utf-8` 明文或 `encoding=base64` + base64 串）。JSON 序列化把 bytes 当二进制编码后，AIO 服务端按 **utf-8 文本**写入磁盘 → 文件内容是整段 base64 字面量。
- **修复**：`aio_sandbox._prepare_write_file_payload`：UTF-8 可解码 → `str` + 默认 utf-8；否则 `base64` + `encoding="base64"`。回归 `test_upload_writes_utf8_text_not_base64_literal`。
- **历史文件**：修复前 session 工作区需手动 base64 解码；新写入正常。

## Docker exec 沙箱替代 AIO 默认路径（2026-07-03）

- **动机**：`ghcr.io/agent-infra/sandbox` ~13GB；Noesis 实际只用 shell/file API，符合 deepagents `BaseSandbox` 官方集成模式（实现 `execute` + upload/download，无需容器内 HTTP）。
- **架构**：`DockerExecSandboxBackend(BaseSandbox)` → runner 内网 API（`/exec`、`/files/read|write`）→ `docker_exec.py`（`docker exec` + `get_archive`/`put_archive`）；lifecycle 仍由 `sandbox-runner` + `sandbox_service` 负责。
- **默认**：`sandbox.backend=docker`；runner `SANDBOX_RUNTIME=docker`；镜像 `deploy/sandbox-slim/Dockerfile`（`noesis/sandbox-slim:latest`，~150MB 级）。`aio` 模式保留（`SANDBOX_RUNTIME=aio` + `agent-sandbox` SDK）。
- **路径策略**：`sandbox_mount_policy.py` 供 docker/aio 共用；容器名 `noesis-sandbox-{hash(user_id)}`（替代 `noesis-aio-*`）。
- **验证**：`test_docker_exec_sandbox_backend.py`、`deploy/sandbox-runner/test_docker_exec.py`；生产前 `docker build -t noesis/sandbox-slim:latest -f deploy/sandbox-slim/Dockerfile .`。

## Docker exec 沙箱对齐与超时（2026-07-04）

- **runtime 对齐**：`PUT /internal/sandboxes/{user_id}` 请求体携带 `{"runtime":"docker"|"aio"}`（来自 `sandbox.backend`）；runner `ensure()` 若内存/标签 `noesis.runtime` 与请求不一致则销毁重建，避免 `docker` backend 误复用 AIO 大镜像容器。`create_docker_exec_sandbox_backend` / `create_aio_sandbox_backend` 二次校验 handle.runtime。
- **exec 超时**：`docker_exec.exec_command` 用 GNU `timeout --signal=TERM` 包裹用户命令；`sandbox_service` 缓存按 expected runtime 失效。
- **httpx**：`DockerExecSandboxBackend` 默认每请求短生命周期 `Client`，测试可注入 `http_client`。

## Docker exec 沙箱冗余清理（2026-07-04）

- **删除**：`sandbox_service` 本地 `_IN_FLIGHT`/`get_in_flight`/`reap_idle_sandboxes`（runner 为 in-flight 权威）；`docker_exec.get_container`（死代码）；`uses_aio_sandbox` 别名；`DockerExecSandboxBackend.container_name`；create 层重复 runtime 校验。
- **合并**：`sandbox_common.py`（`session_mutex`/`prepare_write_file_payload`）；`config.env.sandbox_runner_headers()` 供 service + docker backend 共用。
- **测试**：路径策略集中到 `test_sandbox_mount_policy.py`；transport 测试只保留 runner/SDK 行为断言。

## agent-sandbox 改为可选依赖（2026-07-04）

- **默认 `sandbox.backend=docker`**：backend **不再**安装 PyPI `agent-sandbox`；Agent 经 `httpx` → runner → `docker` SDK。
- **仅 `sandbox.backend=aio`** 时需要 `uv sync --extra aio`（~240KB wheel）；13GB 是 `ghcr.io/agent-infra/sandbox` **容器镜像**，与 pip 无关，docker 模式用 `noesis/sandbox-slim`。

## pyproject 依赖整理（2026-07-04）

- **asyncssh 已删除**：全仓库无 `import asyncssh`；故障运维 MCP（`extensions/mcp/ssh`）用宿主机 `ssh`/`sshpass` subprocess，不依赖该 PyPI 包。
- **依赖安装**：`[dependency-groups] dev`（PEP 735）→ 本地 `uv sync` 默认含 pytest；生产镜像 `uv sync --frozen --no-dev`（uv 对 `dev` 组的官方开关，见 `deploy/backend/Dockerfile`）。
- **config 统一**：`sandbox.backend: docker` 与 `kb.deepdoc` 对齐于 `config.example.yaml`、`config.prod.example.yaml`、`deploy/config.docker.yaml`。

## 本地 docker 沙箱 HTTP 500（2026-07-04）

- **根因**：`noesis/sandbox-slim:latest` 未本地构建时 Docker 隐式 pull Hub 失败 → `APIError` 未捕获 → runner 返回 500。
- **修复**：`manager._ensure_image_available` 先查本地镜像；`main.py` 捕获 `DockerException` 返回 503 + 构建提示；`run.sh` 启动 runner 前自动 `docker build` slim 镜像。
- **本地**：`sandbox.backend=local_shell` 可跳过 runner；docker 模式需 `docker build -t noesis/sandbox-slim:latest -f deploy/sandbox-slim/Dockerfile .`（网络不通时需配置镜像加速）。

## 故障运维 MCP 去掉 Docker 沙箱层（2026-07-04）

- **动机**：Agent 本地执行已由 per-user slim 沙箱隔离；MCP 为受信服务端代码，再套 `mcp-sandbox` 容器边际收益低，且与 `sandbox-runner` 形成两套平行的 docker exec 体系。
- **改动**：`extensions/mcp/docker-ssh/executor.py` 改为宿主机 `subprocess` 调 `ssh`/`sshpass`；删除 `docker_manager.py`、`deploy/mcp/Dockerfile`；`pyproject.toml` 移除 `docker` 依赖；`config.yaml` 仅保留 `ssh.ssh_dir`（默认 `~/.ssh`）。
- **依赖**：MCP 进程所在环境须装 `openssh-client`；`setup_passwordless_login` 另需 `sshpass`。
- **启动**：`START_MCP=1 ./scripts/run.sh dev`，不再构建 `noesis/mcp-ubuntu-ssh` 镜像。
- **收敛（同日后）**：SSH 执行/错误分类统一到 `executor.py`（`exec_remote`、`classify_exec_failure`）；删除 `tools/core._exec_remote`、未使用的 `build_ssh_batch_command`/`strict_host_key_checking`、backend 死函数 `mcp_docker_ssh_dir`；`utils/__init__.py` 去掉未使用的 re-export。
- **目录**：`extensions/mcp/docker-ssh` → `extensions/mcp/ssh`；PyPI 包名 `mcp-ssh`；`extensions_paths.mcp_ssh_dir()`；`MCP_DIR` 默认 `extensions/mcp/ssh`。

## agent_filesystem 冗余收敛（2026-07-04）

- **拆分**：`agent_filesystem.py`（~110 行，仅 `build_agent_filesystem_backend`）+ `prefix_backend.py` + `backend_guards.py`（`GuardedFilesystemBackend` / `StaticListingBackend` / `UserMemoryBackend` 工厂）。
- **`UserMemoryBackend`**：委托 `FilesystemBackend`；路径白名单 + `USER.md` 写拦截；`write` 对已存在文件走 `edit` 全量替换。
- **skills 只读 route（local_shell）**：`FilesystemBackend` 替代 `LocalShellBackend`。

## sandbox-slim 构建 apt 镜像源（2026-07-04）

- **现象**：`debian:bookworm-slim` 已 pull 成功，但 `docker build` 卡在 `RUN apt-get update`（容器内访问 `deb.debian.org` 超时）。
- **修复**：`deploy/sandbox-slim/Dockerfile` 构建前 `sed` 替换为 `mirrors.aliyun.com`；可通过 `--build-arg APT_MIRROR=mirrors.tuna.tsinghua.edu.cn` 覆盖。
- **构建**：`docker build -t noesis/sandbox-slim:latest -f deploy/sandbox-slim/Dockerfile .`（约 3 分钟，镜像 ~82MB content）。
- **后续**：Dockerfile 恢复默认 `deb.debian.org` 源（其他环境网络正常）；国内 apt 超时时可本地临时 `sed` 换镜像或配 Docker/apt 代理，不写入仓库。
