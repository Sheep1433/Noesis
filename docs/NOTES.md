# 知识卡片（开发笔记）

## DeepDoc Vendor 修改清单

| 路径 | 原因 | 同步策略 |
|------|------|----------|
| `backend/kb/deepdoc/parser/resume/`（整目录） | Noesis 知识库仅 PDF/Office/Markdown 入库，不用 HR 简历结构化解析；该目录含 ~5.5 万行词典 JSON/CSV，占 git 体积 | **drop**（合并 upstream 时不带回） |

## 知识库 Agent 工具（2026-07-01）

- **三工具**：`list_knowledge_bases`（发现）、`search_knowledge_base`（片段 hybrid，可传 `collection_names`）、`get_knowledge_document`（整篇补全，80k 字符截断）。
- **检索范围**：工具入参 > 会话 `extra.kb_collections` > 全部可用库；多库 **ThreadPoolExecutor 并行**，每库 `final_top_k=global_limit` 后全局 merge（不再 `ceil(limit/N)` 预截断）。
- **前端**：COMMON_QA 输入区 `KbScopeSelector` 多选写回 `extra.kb_collections`；流式 `extra.kb_collections` 同步会话。
- **检索耗时日志**：`KbRetrievalService.search` → `[KbRetrievalService] search`（recall/parse/rerank/post/total ms）；Agent 跨库 → `[KbSearchTool] search_knowledge_base`（resolve/parallel/merge/total ms）。`grep` 后端 `.data/logs` 或控制台即可。

## 知识库上传与 Rerank 配置（2026-07-01）

- **上传暂存**：`POST .../upload` 写入 `.data/kb_uploads/{collection}/{file_hash}_{原名}`，解析后删除；Qdrant 分片 `file_name` 经 `source_file_name` 显式传入，不再用 `basename(tmp)`。
- **Rerank 密钥**：`ModelSettings.rerank_model_api_key` 在 `config/env.py` 中回退 `embedding_model_api_key`；`RERANK_MODEL_API_KEY` 仅作可选覆盖，prod 不必单独配置。

## 动态切换对话模型（2026-07-02）

- **参考**：LangGraph Runtime / Context 思路——图定义不变，运行时按 `model_id` 选择 LLM 实例；Noesis 采用「配置目录 + 会话 sticky + 请求 extra」而非 middleware 拦截。
- **配置**：`config.yaml` → `model.catalog[]`（id/label/type/name/temperature/base_url）+ `default_catalog_id`；密钥仍用 `.env` `MODEL_API_KEY`。
- **后端**：`get_llm(model_id=...)` → `llm/catalog.py` 解析；`QaService._resolve_model_for_query` 写回 `session.extra.model_id`；Agent `create_noesis_agent(model_id=...)`；assistant 落库 `extra.model`。
- **API**：`GET /api/models`；流式 `extra.model_id`。
- **前端**：输入区 `ModelSelector`（非 TEST_CASE_QA）；切换后 `ensureSession` 持久化。

## OpenCode Zen 免费模型目录（2026-07-02）

- **发现**：`https://models.dev/api.json` 中 `opencode` provider、`cost.input==0 && status==active`；网关 `https://opencode.ai/zen/v1`，`.env` `MODEL_API_KEY=public`。
- **当前可用（实测）**：`deepseek-v4-flash-free`、`big-pickle`、`mimo-v2.5-free`、`nemotron-3-ultra-free`、`north-mini-code-free`；`deepseek-reasoner` **不支持**于 Zen 免费网关。
- **配置**：`config.yaml` → `model.catalog[]` 逐项列出；界面仅展示 catalog 内条目，未写入 catalog 的模型不会出现。

## KB 引用溯源 OpenSpec（2026-07-02）

- **变更**：`openspec/changes/add-kb-citation-sources/`（规格先行，代码未合）。
- **不难（L1–L2）**：`CitationsPart`、snake_case `citations-available`、文末列表 + 抽屉 shard API——与 platform-chat 同构。
- **难（L4）**：**句子 ↔ 分片** 绑定。首版 = LLM 写 `[n]` + 解析；不稳则 **fallback Top-5**（`citation_fallback`，UI 须写清「非逐条引用」）。非审计级归因。
- **L3**：hybrid 默认；`shard_id` = `point_id` 或 **`VectorStorage.hash_to_uuid(content_hash)`**（禁裸 hash）。要 hybrid 集成测。
- **硬约束**：`register_hits` 仅 `_format_hits` 主线程；finalize 在 `to_dict`/stop 文案前；**仅正常 finish** 发 SSE，stop/断连靠**刷新**看来源。
- **二期**：snippet–正文重叠推断 cited；stop 时 SSE 注入来源。

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

## 2026-07-07 — 通用问答图片附件 Vision 优化

- **问题**：`is_vision_available()` 只看全局默认 `model_name`，忽略请求 `model_id`；默认 `qwen-plus` 导致上传图片无法 multimodal 注入。
- **改动**：
  1. `domain/chat/attachments/vision.py`：按 `resolve_catalog_entry(model_id)` 判定；catalog API 增加 `supports_vision`、`first_vision_model_id`、`vlm_fallback_available`。
  2. `ChatAttachmentsMiddleware`：接收 `model_id`；注入前 `prepare_image_bytes_for_injection`（`image_inject_max_edge`）；主模型非 Vision 且 `vlm_fallback_enabled` 时用 `vlm.*` 生成 `[图片描述]` 文本兜底。
  3. 前端上传图片后 `ensureVisionModelForImageUpload` 自动切 catalog 中首个 VL 模型并 toast。
- **配置**：`chat_attachment.image_inject_max_edge`（默认 1536）、`vlm_fallback_enabled`（默认 true）；`config.example.yaml` catalog 增加 `qwen-vl-max`。
- **VLM Key**：`VLM_MODEL_API_KEY` 未设置时回退 `EMBEDDING_MODEL_API_KEY`（与 rerank 一致）；仅配百炼 embedding key 即可启用聊天图片 VLM 兜底。
- **VLM 模型名**：`vlm.name` 须为视觉理解模型（如 `qwen-vl-max`）；`vlm_caption` 仅调用配置的单一模型。
- **preview_base64**：上传图片时生成缩略图 JPEG 写入 DB（最长边 320px、base64 ≤48KB），避免原图 base64 超出 MySQL `TEXT` 65535 上限。
- **图片消息展示**：API 返回 `preview_base64`；`FileListItem` 对 image 展示可点击缩略图（非文件名卡片）；历史消息加载时合并附件元数据。通用上传名（`image.png` 等）落盘为 `img-{id}.ext`。

## 2026-07-08 — web_search DDG「No results found」误报失败

- **现象**：日志 `DDG 搜索失败 … No results found.`，前端 `{"error":"搜索失败"}`；并非网络宕机，是 `ddgs` 在无命中时抛 `DDGSException`。
- **根因**：`mojeek,yandex` 对长中文 query（如定价表）常 0 条；原逻辑把该异常当基础设施失败。
- **修复**：`ddg.search_with_ddg` 将无结果视为 `total_results=0` 正常返回，并按 `配置 → mojeek → duckduckgo` 依次尝试；仅全链路超时/连接失败才返回 `error`。
- **建议**：国内稳定搜索配置 `TAVILY_API_KEY`；DDG 无结果时 Agent 仍可依赖图片 VLM 描述作答。
- **DDG 引擎实测（2026-07-08，本机网络）**：`ddgs` 文本源共 9 个——`mojeek` 3/4 命中（唯一天气失败）；`brave` 仅英文定价 1/4；`yandex/duckduckgo/google/startpage/wikipedia/yahoo/grokipedia` 基本超时或无结果。无百度/搜狗。默认改为 `ddg_backends: mojeek`，代码回退链 `mojeek → brave → duckduckgo`。

## 2026-07-09 — 会话上下文面板展示用户目录

- **需求**：右侧文件面板除当前会话 `workspace/uploads` 外，展示用户级 `skills/`、`AGENTS.md`、`USER.md`；`sessions/` 仅列当前 `session_id`，避免历史会话文件撑爆树。
- **实现**：`SessionContextService.get_context` 根节点改为 `users/{uid}`；`read_workspace_file` 根改为 `get_user_root`，路径白名单含 `skills/`、记忆文件与 `sessions/{sid}/workspace|uploads`；旧 `workspace/` 前缀仍兼容。
- **Spec**：`openspec/specs/chat-session-context-panel/spec.md` 同步为 `tree` 契约（取代早期 `workspace`+`attachments` 字段描述）。

## 2026-07-09 — 会话面板下载/编辑 + Mermaid 渲染

- **下载/编辑**：`FilePreview` 工具栏增加下载、编辑（仅 `getFilePreviewKind === 'text'`）；`PUT /api/chat/sessions/{id}/workspace/file` 写回 `SessionContextService.write_workspace_file`，路径白名单与 GET 一致、512KB 上限。
- **Mermaid**：`markdown-it` 增加 `mermaidPlugin`（` ```mermaid ` 围栏 → `<div class="mermaid">`）；`useMermaidRender` 在 `MarkdownPreview`（主对话）与 `FilePreview`（侧栏 MD 预览）挂载后 `mermaid.run()`，300ms debounce 避免流式中途反复渲染。
- **复杂文件**：Office/PDF 仍走 artifact 新标签打开，无内联编辑。

## 2026-07-09 — 会话面板目录打包下载

- **API**：`GET /api/chat/sessions/{id}/workspace/archive?path=` → `SessionContextService.build_path_archive`，递归 zip；白名单含 `skills/`、`sessions/{sid}/workspace|uploads` 及任意子目录；禁止 `users/{uid}` 根；总内容 20MB 上限。
- **前端**：`WorkspaceFileTree` 节点右键「打包下载」；根节点 `users/{uid}` 不显示菜单；单文件也会打成一个 ZIP。

## 2026-07-09 — 智能体落盘路径：默认工作区根，research 仅调研场景

- **问题**：`SUPER_AGENT_QA` prompt 将 `/research/` 宣称为默认可写区，导致「画 mermaid 并保存」等普通任务也被写入 `workspace/research/`。
- **改动**：`super_agent.py` + `execution.py` 明确通用任务默认落盘到工作区根或任务自建子目录；`/research/` 仅在激活 `deep-research-v2` 等 research Skill、或用户明确要求 research 式目录时使用。路径路由层不变（`/research/foo` 仍映射到 `workspace/research/foo`）。
- **Spec**：`agent-sandbox`、`agent-runtime-paths` 同步默认落盘语义。

## 2026-07-09 — 后端 Docker 镜像瘦身

- **现象**：生产 `noesis-backend` 镜像约 6GB 虚拟大小（独占 ~1.8GB），偏大。
- **根因**：单阶段 Dockerfile 将 `build-essential`、`gh` 等构建工具留在最终层；`fastapi[all]` 拉入 fastapi-cli / cloud-cli 等生产无用依赖；`evals/`、`tests/` 打进构建上下文。
- **改动**：`deploy/backend/Dockerfile` 改为 builder + runtime 多阶段；runtime 仅 `curl` + `libmariadb3`；`pyproject.toml` 用 `fastapi` + `uvicorn[standard]` + `python-multipart` 替代 `fastapi[all]`；`.dockerignore` 排除 `backend/evals`、`backend/tests`；compose healthcheck 改 `curl`。
- **未动**：DeepDoc 相关 `opencv` / `onnxruntime` / `sklearn` 仍为业务必需，模型权重继续卷挂载。

## 2026-07-09 — OpenCode deepseek-v4-flash-free 思考流与 trust_env 网络路径

- **现象**：默认模型 `deepseek-v4-flash-free` 聊天页不再展示「思考过程」折叠块；`show_thinking_process: true` 且 `LangGraphSseBridge` / 前端 `ReasoningBlock` 链路正常。
- **易误判**：不是 `trust_env` 字段本身「控制」`reasoning_content`；它只是 httpx 是否读取系统代理的开关。
- **因果链**：`bf10552` 在 `llm/factory.py` 为全部 LLM 注入 `trust_env=False` 的 httpx 客户端（绕过 macOS 系统代理 `127.0.0.1:10810`，避免代理挂掉时 OpenCode/DashScope `APIConnectionError`）→ 直连 vs 走代理落到 **不同 Cloudflare 边缘 / 出口** → 同模型同 prompt 的 SSE `delta` 形态不一致。
- **本机实测（2026-07-09）**：
  - `trust_env=False`（Noesis 当前默认）：直连 `opencode.ai`，`cf-ray=…-BOS`，流式 `reasoning_content` **0 个非空 chunk**（5/5 次稳定）。
  - `trust_env=True`：经 `127.0.0.1:10810`，`cf-ray=…-EWR`，`reasoning_content` **数百～上千 chunk**（5/5 次稳定）。
  - 目录内 `big-pickle` 在直连下仍有 `reasoning_content`；flash-free 对网络路径更敏感。
  - flash-free 有时把「逐步推理」写进正文 `content`，不会进 `ReasoningBlock`。
- **结论**：根因偏 **OpenCode / CDN 路由侧**对不同 egress 返回字段不一致，不是 Noesis reasoning 解析回归。
- **暂缓修复**：暂不改动 `trust_env` 策略；需稳定思考时可切 `big-pickle`，或日后仅对 `opencode.ai` 单独恢复代理 egress。

## 2026-07-10 — 会话面板目录递归打包下载

- **API**：`GET /api/chat/sessions/{id}/workspace/archive?path=` → `SessionContextService.build_path_archive`，递归 ZIP；白名单含 `skills/**`、`sessions/{id}/workspace|uploads`、单文件；禁止 `users/{uid}` 根；总大小上限 20MB。
- **前端**：文件树节点右键「打包下载」；`downloadWorkspaceArchive` 解析 `Content-Disposition` 触发浏览器保存。
- **Bug（2026-07-10）**：`WorkspaceFileTreeNode` 子节点误传本地 `onContextMenu` 包装函数，导致除 `users/{uid}` 根外所有层级右键失效；Mac 双指点击无菜单。修复为透传 prop `onContextMenu`，`nextTick` 后再 `show` 下拉，并 `to="body"` 避免被侧栏裁剪。
- **下载体验（2026-07-10）**：菜单文案改为「下载」；单文件直接下原始文件，目录才打 ZIP；ZIP 写入时钳制 `mtime < 1980`（及 Py3.12 `strict_timestamps=False`）修复 `ZIP does not support timestamps before 1980`。
- **根因修复（2026-07-10）**：`deploy/sandbox-runner/docker_exec.py` 写文件时 `TarInfo.mtime` 默认 0 → 宿主机 bind mount 文件 mtime 为 1970；现设为 `int(time.time())`。已有旧文件 mtime 不变，需 Agent 重写或 `touch` 才更新。

## 2026-07-10 — SuperAgent 用户记忆（add-super-agent-user-memory 归档）

- **BREAKING**：`DEEP_RESEARCH_QA` / `DeepResearchAgent` 移除；统一为 `SUPER_AGENT_QA` + `SuperAgent` + `task-worker`。
- **磁盘**：`.data/users/{uid}/AGENTS.md`（Agent 可写）、`USER.md`（Agent 只读）；删 session **不**删记忆文件。
- **虚拟路径**：`/memory/AGENTS.md`、`/memory/USER.md` → `UserMemoryBackend`；`MemoryMiddleware` + `MemorySyncMiddleware` 仅主 Agent 挂载。
- **面板**：右侧上下文树展示两文件；`PUT workspace/file` 允许用户直接编辑 `AGENTS.md` 与 `USER.md`。
- **Agent 写边界（2026-07-10 更新）**：`USER.md` 与 `AGENTS.md` 均可由 Agent `edit_file` 更新（对齐 OpenClaw「USER.md Update as you go」）。
- **规格**：主 spec 新增 `agent-super-agent`、`agent-user-memory`；删除 `agent-deep-research`。
- **测试**：`test_super_agent_memory.py`、`test_user_memory_*`、面板写 `USER.md` 用例。

## 2026-07-11 — 前端移动端响应式适配

- **基础设施**：`config/breakpoints.ts`（xs/sm/md/lg 统一断点）、`hooks/useBreakpoint.ts`（`isMobile` = `<1024px`）、`hooks/useResponsiveDrawerWidth.ts`（抽屉宽度阶梯）、`styles/tokens/_layout.scss`（壳层内边距、底栏高度、`100dvh`、safe-area）。
- **壳层**：`SlotCenterPanel` 在移动端隐藏 280px 侧栏，改为 `MobileBottomNav` 固定底栏；主区 padding/圆角缩小并预留底栏 + safe-area。
- **对话页**：历史列表抽为 `ChatHistoryPanel.vue`；桌面仍用 `n-layout-sider` + 拖拽，移动端用左侧 `n-drawer` + 顶栏菜单钮；会话文件区移动端用右侧 `n-drawer`（与历史抽屉互斥）；QA 模式 tabs 横向滚动；消息/输入区 gutter 窄屏改为 12px。
- **未覆盖**：`TestAssistant`、知识库表格等仍桌面优先，可复用同一套 `useBreakpoint` + drawer 模式后续迭代。

## 2026-07-11 — Skills / 知识库移动端适配

- **SkillsManagement**：技能目录抽为 `SkillsTreePanel.vue`；移动端隐藏 320px 侧栏，顶栏「技能目录」打开左侧抽屉，选中可预览文件后自动收起；上传弹窗宽度随视口收缩。
- **KnowledgeBase**：列表页单列卡片、状态栏纵向堆叠；新建弹窗 `min(520px, 100vw - 32px)`。
- **CollectionDetail**：页头操作换行；Tab 窄屏改 `segment`；文档表精简列（文件名/分片/操作）+ 横向滚动兜底；上传弹窗响应式宽度。

## 2026-07-11 — 对话模型 per-model 上下文（catalog limit）

- **字段**：`model.catalog[].limit` 与 models.dev / OpenCode 一致：`context`（窗口上限）、可选 `output` / `input`；`model.limit` 可作无 catalog 或 catalog 项未写 limit 时的默认。
- **解析**：`llm/model_limits.resolve_model_limit(model_id)` → catalog `limit.context` → `context.max_input_tokens`（全局兜底）→ 128K。
- **运行时**：`create_noesis_agent(model_id=...)` 将 `model_id` 传入 `SummarizationOffloadMiddleware` 与 `ContextMetricsMiddleware`；压缩触发默认 `trigger_tokens: 0` + `trigger_fraction × limit.context`。
- **API**：`GET /api/models` 每项返回解析后的 `limit` 对象；embedding/rerank 配置未改。

## 2026-07-11 — sandbox-runner 残留容器 409 修复

- **现象**：zzqroot 重部署 compose 后 `SUPER_AGENT_QA` 对话无正文；backend 报 `创建用户沙箱失败 HTTP 503`，Docker 409 同名容器已存在（`Exited` 状态）。
- **根因**：`sandbox-runner` 内存 `_records` 清空后 `_sync_running()` 对非 running 容器返回 `None` 但不删除；`ensure()` 直接 `_start_container` 触发名称冲突。
- **修复**：`deploy/sandbox-runner/manager.py` 新增 `_cleanup_stale_container()`，在 `_start_container` 前移除 exited/dead 容器；单测 `test_ensure_runtime.py` 覆盖。
- **运维**：已手动 `docker rm noesis-sandbox-*` 恢复线上；后续发版 runner 镜像后自动免疫。

## 2026-07-11 — 前端 HTTP 环境复制失败

- **现象**：zzqroot（`http://43.134.128.65:28468`）点击对话/知识库「复制」提示失败。
- **根因**：非安全上下文（HTTP）下 `navigator.clipboard` 不可用；曾短期加 `execCommand` 降级。
- **现状**：去掉 `execCommand` 降级，仅保留 HTTPS 下 Clipboard API；待海外机绑定域名 + 免费证书后再启用复制。

## 2026-07-13 — MySQL → PostgreSQL 生产迁移

- **动机**：统一业务库与 LangGraph checkpoint 到 PostgreSQL；Compose 内建 `postgres:17`，去掉宿主机 `noesis-mysql` 容器。
- **代码**：`database.py` / `env.py` 改 `asyncpg`+`psycopg`；`checkpointer` 用 `noesis_langgraph` 库；Alembic 迁移与 `initialize_postgresql.py`；JWT 改为服务端 Session（`auth_api` + `user_sessions` 表）。
- **部署**：`deploy/docker-compose.yml` 增 `postgres` 服务；`config.docker.yaml` 中 `database.host: postgres`；`deploy/postgres/init.sql` 建 checkpoint 库；`.env.docker` 用 `POSTGRES_PASSWORD`（宿主机映射端口与 Langfuse 冲突时用 `POSTGRES_PORT=5433`）。
- **注意**：线上 MySQL 卷已删则无法自动迁数据，需空库 Alembic 初始化；CI `main` 通过后 `deploy-remote.sh` 自动重建栈。

## 2026-07-21 — Composer 会话级 Models / Skills / MCP

- **动机**：补齐用户自定义 MCP + Cursor 式 Composer 勾选；此前 MCP 仅部署级 `mcp.json` 且绑死 `FAULT_OPERATION_QA`。
- **配置**：平台 `extensions/mcp/mcp.json` + 用户 `.data/users/{uid}/mcp.json`（同名用户覆盖）；用户仅允许 `streamable_http`/`sse`。
- **会话 extra**：`mcp_servers`（缺省：FAULT 回退 profile，其它 `[]`）、`enabled_skills`（缺省全部）。
- **API**：`/api/mcp/servers` list/PUT/DELETE/probe；打开菜单只拉元数据，不 `get_tools()`。
- **Agent**：COMMON / FAULT / SUPER 均可按勾选挂 MCP；SUPER 按 `enabled_skills` 过滤 SkillsMiddleware sources；TEST_CASE 协调器不挂 MCP。
- **UI**：`ChatComposerToolbar` `+` 菜单含 Models / Skills / MCP；添加 MCP 对话框写用户配置。
- **OpenSpec**：`openspec/changes/composer-session-tools/`。

## 2026-07-21 — 沙箱收敛：Shell 操作符 / Compose bind / session 隔离

- **根因**：不是双 Skills 路径，而是整用户 rw 挂载 + execute `shlex.split`→`shlex.join` 改写 + Compose 把 runner 容器内路径交给 daemon，叠加 AIO 兼容链。
- **P0 修复**：
  1. 删除 execute 虚拟路径 rewrite（`path_rewrite.py`）；Shell 保留 `>`/`|`/`&&`。
  2. Compose：`NOESIS_HOST_DATA_DIR` / `NOESIS_HOST_SKILLS_DIR` 必须是宿主机绝对路径，并以同路径字符串 bind 进 runner。
  3. 挂载收敛：session workspace → `/workspace` rw；公共/个人 Skills → `/skills/public|personal` ro；不再挂整棵 `users/{uid}`。
- **P1**：per-session 容器；handle 404 清缓存并重建；删除 AIO / `agent-sandbox`；slim 非 root（UID 10001）；去掉递归 chmod 644。
- **P2**：上传/删除个人 Skills 后 `bump_user_skills_revision` + `RevisableSkillsMiddleware` 强制重扫。
- **验证**：`backend` 487 passed；`deploy/sandbox-runner` 13 passed。Compose 真机验收见 `deploy/README.md` 清单。
- **OpenSpec**：`openspec/changes/converge-agent-sandbox/`。

## 2026-07-21 — SuperAgent prompt：复杂任务默认委派 task-worker

- **动机**：对话六七轮 + 主线程连环 `web_search`/`web_fetch` 易触上下文上限；暂不改 middleware/配置，先用 prompt 约束行为。
- **改动**：`prompts/super_agent.py` 翻转策略——轻量（≤2 次工具）主 Agent 自做；多源检索/调研/多步实现优先 `task-worker`；委派须自包含、禁懒委派；子 Agent 小结默认 ≤400 字、长文落盘。`task-worker` tool description 同步强调优先委派。
- **局限**：仅靠 prompt，主 Agent 仍持有 web/fs 工具，模型可能继续自己搜；若仍爆窗，再做 eager offload / 工具白名单。

## 2026-07-21 — MCP 配置页：文件编辑 + 状态展示

- **交互**：参考 Cursor——配置在 `users/{uid}/mcp.json` 文本编辑；管理页左侧状态（连通/tools），右侧编辑器；「检测连通」批量 probe。
- **API**：`GET/PUT /api/mcp/config`；`GET /api/mcp/servers/status?probe=`。
- **Composer**：会话勾选保留；「打开 MCP 配置…」跳转管理页（侧栏新增 MCP）。
- **约束**：用户配置仍禁止 stdio，仅 streamable_http/sse。
- **体验修正**：进入页/保存后**自动 probe**（对齐 Cursor 打开即绿点）；去掉逐行「探测」；布局对齐 Skills 管理页 sider+editor。

## 2026-07-21 — MCP 目录与配置对齐（Context7 + remote_ops）

- **问题**：管理页状态列出平台 `fault_ops`/`ssh`，右侧编辑器却是空 `mcpServers`，两边不一致；连接失败日志只见 `TaskGroup` 笼统信息。
- **平台默认**：`extensions/mcp/mcp.json` 改为 `context7`（https://mcp.context7.com/mcp）+ `remote_ops`（`${NOESIS_MCP_REMOTE_URL}`，默认 `http://localhost:8000/mcp`）；FAULT/simple_mcp profile 指向 `remote_ops`。
- **用户 seed**：无文件或 `mcpServers` 为空时写入与平台相同的两项，保证编辑器 ↔ 状态列表一致。
- **管理页 scope**：`/servers/status` 默认 `scope=user`；Composer 目录仍用 `scope=all` 合并视图。
- **环境变量**：`CONTEXT7_API_KEY`、`NOESIS_MCP_REMOTE_URL`；未设密钥置空，未设远程 URL 用默认 localhost。
- **日志**：loader 展开 ExceptionGroup / TaskGroup 子异常，并打印目标 URL。

## 2026-07-21 — MCP display_name 导致假绿点

- **现象**：管理页全绿，日志却是 `TypeError: _create_streamable_http_session() got an unexpected keyword argument 'display_name'`。
- **根因**：`mcp.json` 的 `display_name` 被原样塞进 `MultiServerMCPClient`；loader 吞异常返回空列表后，probe 仍因「server 已配置」标 `ok=True`。
- **修复**：`to_adapter_connection()` 白名单过滤连接字段；probe 直接 `get_tools()`，失败则 `ok=False`。

## 2026-07-21 — MCP status 慢 + 未套 ResponseUtil

- **为何 6s+**：`probe=true` 对每个 server 真实 `get_tools()` 握手；并行后总耗时 ≈ max（Context7 跨境 RTT 常占大头；`remote_ops` 未启动则失败）。
- **响应格式**：`/api/mcp/*` 统一 `ResponseUtil.success(data=...)`；前端 `parseAuthJson` 解包。
- **优化**：探测 HTTP timeout 4s + `asyncio.wait_for`；结果缓存 45s；状态 URL 展开 `${ENV}`；错误展开 TaskGroup 子异常。

## 2026-07-21 — Skills 市场对接 skills.sh

- **选型**：接 skills.sh（跨 Agent 公共目录），不接 ClawHub（偏 OpenClaw）。
- **发现**：`GET /api/skills/market/search` → `skills.sh/api/search`；`/market/browse` 用配置 `skills_market.featured_skills` 推荐种子。
- **安装**：`POST /api/skills/market/install` 从 GitHub zipball 抽出含 `SKILL.md` 的目录 → 写入 `.data/users/{uid}/skills/`，写 `.skills-sh/origin.json`，bump revision。
- **UI**：扩展页 Skills 增加「已安装 / 市场」Tab；装完回到已安装树。
- **配置**：`backend/config.yaml` → `skills_market.*`（base_url / timeout / featured_skills）。

## 2026-07-21 — Skills 市场：热度 + 已安装态

- **推荐热度**：browse 经 `resolve_featured` 按 skill_id search 补 `installs`（带 cache_ttl）。
- **安装态**：条目带 `install_match`：`exact`（origin.source+skillId 同源）/ `name_conflict`（仅同名目录）/ `none`。
- **重名策略**：落盘仍用 `skill_id` 单目录；冲突需覆盖，不并存多源同名包。

## 2026-07-21 — Skills 市场推荐改为 skills.sh 趋势榜

- **原因**：网页 Leaderboard 即趋势；原先对 featured 搜 8 次是多余。
- **实现**：`fetch_trending` 一次 GET 首页 HTML，解析 SSR 榜单（rank + installs）；缓存 `cache_ttl`；`site/*` 非 GitHub 源跳过。
- **回退**：解析失败仍用配置 `featured_skills`。

## 2026-07-21 — Skills 市场：All Time + Trending，取消回退

- **排序**：`GET /market/browse?sort=trending|all_time` → 抓 `skills.sh/trending` 或 `skills.sh/`。
- **无回退**：榜单失败直接报错，不再用 featured 种子顶上。
- **UI**：市场 Tab 提供 Trending / All Time 切换。

## 2026-07-21 — Composer `/` `@` mentions（slash / at）

- **动机**：对齐 Cursor 输入内快速引用；Web 无本机索引，用预取 catalog + 本地 fuzzy + 结构化 `mentions`。
- **协议**：请求 `extra.mentions[]`（`skill|file|folder|subagent`）→ `MentionResolveService` 校验归属/穿越 → prompt `<user_mentions>` 注入；user 消息 `extra.mentions` 落库。
- **范围**：SUPER 全开；FAULT 仅 file/folder/subagent（skill → 4xx）；COMMON/TEST 拒 mentions。
- **性能**：复用 `GET /api/skills/fs/tree` + session context；TTL 缓存；按键不打全量树。
- **UI**：`MentionPicker`；选中/Tab 将 `/skill`、`@path` **直接写入输入框**（无上方 chip）；`/` 仅**行首**，`@` 为**空白边界**（可一句内挂多文件）。历史气泡仍可只读展示 `extra.mentions`。OpenSpec：`add-composer-slash-mentions`。

## 2026-07-21 — API 响应统一 ResponseUtil 补齐

- **问题**：除 MCP 外，Skills GET、知识库几乎全部、Chat context/workspace 文件仍返回裸 Pydantic，违反 `backend/AGENTS.md`「禁止手写裸 JSON」。
- **已对齐**：
  - `skill_api`：fs/tree、fs/file、market browse/search/detail
  - `knowledge_base_api`：status/collections/config/documents/shards/upload/delete 等 JSON 端点（search 原本已套）
  - `chat_api`：session context、workspace file GET/PUT
- **前端**：`skills.ts` / `knowledgeBase.ts`（`kbJson`）/ `chat.ts` 对应函数改用 `parseAuthJson` 解包 `data`。
- **刻意不套**：SSE StreamingResponse、workspace archive / 附件 FileResponse、导出 markdown 等二进制流。
- **本来就合规**：auth / user / model / chat 会话消息主路径 / mcp / chat_attachment。

## 2026-07-21 — MCP probe：Context7 超时误杀 + remote_ops 502

- **Context7**：4s 探测超时过紧（跨境偶发 >4s），改为 12s；成功缓存 60s、失败仅 8s，避免把超时结果锁死。
- **remote_ops 502**：本机 `extensions/mcp/ssh` 未正常起来（默认 `START_MCP=0`）。提示改为引导 `START_MCP=1 ./scripts/run.sh dev`；启动脚本显式 `--transport http --port 8000`。

## 2026-07-21 — 用户 MCP 配置改为字面量

- **结论**：个人 `users/{uid}/mcp.json` 面向终端用户，应直接写 URL / headers；`${ENV}` 只留给平台 `extensions/mcp/mcp.json`（部署密钥、不进用户编辑器）。
- **改动**：seed 默认 `https://mcp.context7.com/mcp` + `http://localhost:8000/mcp`；保存时拒绝 `${`；打开配置时把历史占位展开写回；UI 文案去掉「支持环境变量」。

## 2026-07-21 — Skills 市场：深层路径 + 错误码

- **安装失败根因**：`find_skill_dir` 只查 `skills/{id}` 等常见布局；榜单项如 `101-skills/skills/ai-video-generation` 实际在 `tools/video/...`，`getpaperclipai/paperclip/design-guide` 在 `.claude/skills/...`。
- **修复**：`rglob` 按目录名匹配 `{skill_id}/SKILL.md`，多命中时优先浅路径与 `skills/` 下。
- **错误码**：未找到技能/仓库 → `NotFoundException`（HTTP 404）；GitHub 瞬断 → `ServiceWarning`（601）+ 下载重试一次；避免 HTTP 400 却 `code: 500`。

## 2026-07-21 — Skills 市场详情加速 + 包内目录树

- **慢因**：`detail` 为读 `SKILL.md` 整仓 zip（如 paperclip ~21s）。
- **修复**：`fetch_skill_preview` 用 GitHub `git/trees?recursive=1` 定位技能目录 + `raw.githubusercontent.com` 拉正文；响应增 `skill_tree`；安装仍走 zip。

## 2026-07-21 — Skills 市场详情：60s 超时根因

- **现象**：浏览器开 skills.sh 很快，本地 `/market/detail` 偶发 ~60s。
- **根因**：详情走 GitHub 全仓 `trees?recursive=1`（paperclip ~1.1MB JSON），国内 GitHub API 易卡住直至 `github_timeout_seconds=60`；skills.sh 页面是 SSR 缓存，不经过该链路。
- **修复**：详情改为 raw 并行探测 → skills.sh 页解析路径 → Contents API 列包内文件；全仓 trees 仅最后兜底；preview 用 `search_timeout`(15s)，与安装 zip 的 60s 分离；detail 内 preview 与 search 并行。

## 2026-07-21 — 知识库检索成本：缩小 rerank 输入

- **动机**：hybrid 默认 `recall_top_k=50` 全量送 DashScope rerank，按 documents 计费；日志常见 `rerank_ms≈1.7s`、`recall_hits=50`。
- **改动**：
  - 默认 `recall_top_k` 50→**20**，新增 `rerank_top_k` 默认 **15**（且 `min(rerank, recall)`）。
  - `KbRetrievalService`：召回排序后只截 `rerank_top_k` 再调 API；日志增 `rerank_docs` / `rerank_top_k`。
  - 集合配置 / 检索调试 UI 可改该参数。
- **注意**：已写入 PostgreSQL `query_params.recall_top_k=50` 的集合不会自动变；需在策略页保存新值或清掉该项吃平台默认。

## 2026-07-21 — RevisableSkillsMiddleware 缺 config 参数

- **现象**：超级智能体启动报 `SkillsMiddleware.abefore_agent() missing 1 required positional argument: 'config'`。
- **根因**：deepagents `SkillsMiddleware.(a)before_agent` 已增加 `config: RunnableConfig`；本地子类覆盖仍按旧签名 `(state, runtime)` 调用 `super()`。
- **修复**：`revisable_skills_middleware.py` 同步接收并转发 `config`。

## 2026-07-21 — Skills 市场详情：仅 skills.sh，不展示包内目录

- **约定**：浏览/榜单/详情走 skills.sh；安装仍走 GitHub zip；完整目录结构仅在「已安装」Tab 查看。
- **实现**：`fetch_skill_preview` 只 GET `skills.sh/{source}/{skill_id}`，解析 `prose` 正文 + schema.org `display_name`；响应去掉 `skill_tree`；前端详情区移除目录树，提示安装后去已安装查看。
- **清理**：删除 GitHub trees/Contents 预览与 `_build_market_tree` 等死代码。

## 2026-07-22 — Skills 市场详情：解析 previewHtml + restHtml

- **现象**：详情只到「2. Tech Stack」并混入 `Show more` / Installs 等侧栏文案。
- **根因**：skills.sh SSR 的 `prose` 区只渲染折叠前预览；完整正文在 RSC flight 的 `previewHtml` + `restHtml`（`$34`/`$35` 槽位）。
- **修复**：`_extract_preview_rest_html` 拼接两段 HTML 再转 Markdown；SSR `prose` 仅作回退。

## 2026-07-22 — SuperAgent HITL（工具审批 / ask_user）

- **与测试用例 interrupt 区别**：测试用例是 `interrupt_before` 节点级暂停 + `Command(update=...)`；HITL 是 `HumanInTheLoopMiddleware` 工具级 `interrupt(HITLRequest)` + `Command(resume={"decisions":...})`。
- **SSE 形状**：对齐 `test-case/resume`——首段发 `hitl-required` 后以 `finish_reason=hitl_pending` + `[DONE]` 收尾，**不** completed 落库；resume 新开 SSE，续写同一 `assistant_message_id`。
- **关键模块**：`agent/hitl/`（policy / ask_user / session_grants / pending / timeout）、`create_noesis_agent(interrupt_on=...)`、`POST .../hitl/resume`。
- **配置**：`hitl.enabled`（默认 false）、`hitl.ask_timeout_seconds`（默认 300）。

## 2026-07-22 — HITL 少问策略 + Composer 翻页面板

- **少问**：`is_dangerous_execute` 仅网络出口 / pipe-to-shell；`rm -rf` 等沙箱内破坏命令不再打断。`/memory/**` 写入仍审批。
- **一点全过根因**：LangChain 要求 `decisions` 与 `action_requests` 等长；旧 UI `map(() => approve)` 把整批一次放行。
- **UI**：气泡内联卡移除；输入区上方（Todo 同槽）`HitlComposerPanel` 左右翻页，本地累积 draft，审批最后一条自动 resume，提问全部答完点 Continue 再 resume。
- **有 pending HITL 时优先显示面板、暂隐 Todo**。

## 2026-07-22 — Skills 市场详情：改走 /api/download

- **动机**：HTML/RSC 解析脆弱且曾截断；`/api/download` 直接返回 `SKILL.md` 原文，更稳更快。
- **实现**：`fetch_skill_preview` → `GET skills.sh/api/download/{source}/{skill_id}`，只取 `files[]` 中 `SKILL.md`；`name` 从 frontmatter 读；仍不写本地、不展示包内目录。
- **清理**：移除详情页 HTML / RSC 解析辅助函数。

## 2026-07-22 — Skills 市场：SKILL 详情独立长 TTL 缓存

- **机制**：进程内 dict + `time.monotonic()`；key=`{source}/{skill_id}`；仅 `fetch_skill_preview`（/api/download）使用 `preview_cache_ttl_seconds`。
- **默认**：搜索/榜单 `cache_ttl_seconds=300`；SKILL 详情 `preview_cache_ttl_seconds=86400`（24h）。
- **配置**：`skills_market.preview_cache_ttl_seconds` 或环境变量 `SKILLS_MARKET_PREVIEW_CACHE_TTL_SECONDS`；`0` 关闭缓存。

## 2026-07-22 — skills.sh 请求：重试 + 过期缓存回退

- **现象**：榜单偶发 `[SSL: UNEXPECTED_EOF_WHILE_READING]`，TTL 过期后无缓存可回退即失败。
- **修复**：`_request_skills_sh` 禁用 HTTP/2、加 UA、最多 3 次指数退避重试；榜单/搜索/详情在请求失败时回退 stale 缓存（最长 `max(cache_ttl, preview_cache_ttl)`）。

## 2026-07-22 — 配置示例与远程部署对齐

- **问题**：远程 `config.prod.yaml` / `deploy/config.docker.yaml` 缺近期新增段（`skills_market`、`hitl`、`agent_runtime.tool_call_limit`、`chat_attachment` 部分字段等），仍用代码默认值。
- **同步**：补齐 `config.example.yaml`、`config.prod.example.yaml`、`deploy/config.docker.yaml` 与 `.env*.example`（`SANDBOX_RUNNER_TOKEN` 等密钥项）。

## 2026-07-22 — .env 与 config.yaml 职责划分

- **约定**：`.env` 仅密钥/Token/`APP_ENV`；`hitl`、`skills_market`、超时、开关等运行参数只在 `config.yaml`。
- **清理**：从 `.env*.example` 移除 `HITL_*`、`SKILLS_MARKET_*`；`_build_hitl` / `_build_skills_market` 不再读对应环境变量覆盖。

## 2026-07-22 — Chat 对话面生命周期（设计定稿）

- **问题**：刷新/挂载 ModelSelector → `ensureSession` 造空「新对话」；方案 B 附件与 Composer 偏好物化时机冲突；FAULT `+` 仍可能走 KB 上传。
- **定稿**：L2 Staging+Commit；状态机 DRAFT→COMMITTING→ACTIVE；偏好 User defaults ⊥ draft overlay ⊥ `session.extra`；列表不展示 draft/空壳；ACTIVE 用 `/chat/:id` 续聊。
- **文档**：`docs/prd/platform/Chat对话面生命周期设计.md`；规格 `openspec/specs/chat-surface-lifecycle/spec.md`（演进 `chat-composer-send-upload`）。
- **落地顺序**：P0 禁挂载 ensure + 滤列表 + 关 FAULT 上传 → P1 路由续聊 → P2 staging/TTL → P3 用户默认设置与 TestAssistant 收编。

## 2026-07-22 — Chat 对话面：去掉 draft，改回发送才物化

- **修订**：产品确认未发送附件无需服务端保存，刷新丢失合理；删除 draft / staging / soft lifecycle。
- **现行定稿**：COMPOSING→SENDING→ACTIVE；点击发送才 ensure；附件保持方案 B（本地队列）；偏好三层仍在，overlay 仅内存。
- **文档已改**：`docs/prd/platform/Chat对话面生命周期设计.md`、`openspec/specs/chat-surface-lifecycle/spec.md`；落地 P0→P1→P2（无 staging 阶段）。

## 2026-07-22 — Chat Surface P0 实现（feat/chat-surface-lifecycle）

- **前端**：ModelSelector / KbScopeSelector / Toolbar 增加 `persistSessionExtra`；COMPOSING 不 ensure；发送前 `ensureSession(buildComposingSessionExtra)`；FAULT 隐藏上传入口且不用 kb 即时上传。
- **后端**：`get_user_sessions` / `query_user_sessions_for_record` 增加「至少一条未删 user 消息」exists 过滤。
- **测**：`tests/test_session_list_hides_empty.py`。

## 2026-07-22 — Chat Surface P1：/chat/:sessionId 续聊

- **路由**：`ChatIndex`（`/chat`）、`ChatNew`（`/chat/new`）、`ChatSession`（`/chat/:sessionId`）。
- **行为**：发送 ensure 成功后 `replace` 到 ChatSession；历史点选同步 URL；刷新 ACTIVE 经 `getSession` + `loadSessionMessages` 恢复；无 user 消息的深链回新对话；`newChat` → ChatNew。
- **导出**：`loadSessionMessages` 供路由恢复复用。

## 2026-07-22 — Chat Surface：User defaults 本期不做

- 跨会话默认模型/MCP/Skills/KB 仍只靠平台缺省 + COMPOSING 内存 overlay + 发送后 `session.extra`。
- 个人默认若以后要做，挂 `add-agent-user-settings`；TestAssistant 不在本 lifecycle 收编范围。

## 2026-07-22 — 关「管理对话」洗白主区聊天记录

- **现象**：点「管理对话」再叉号关闭后，侧栏仍在，主区变回默认欢迎页（像刷新）。
- **根因**：`handleModalClose` 强制 `showDefaultPage=true` + `navigateToComposingUrl`；且 `fetchConversationHistory(isInit)` 在无 row 时清空 `conversationItems`。
- **修复**：关闭弹窗只刷新左侧列表；当前会话若仍存在则保留对话面；仅当会话已在弹窗内删除才回 composing。列表刷新不再清空主区 messages。

## 2026-07-23 — 移动端布局与 MCP 状态 400

- **MCP 400**：生产镜像只 COPY `extensions/skills`，缺 `extensions/mcp/mcp.json`；`probe_server` → `get_merged_server_map` → `load_mcp_json` 抛 `FileNotFoundError`，全局 handler 变成 HTTP 400。修复：`deploy/backend/Dockerfile` 增加 COPY `extensions/mcp`；`load_mcp_json` 缺文件时回退空配置；MCP 管理页探测失败降级 `listMcpServers(user)`。
- **Skills 市场移动端**：列表/详情不再上下堆叠；点选后全屏详情 +「返回列表」。
- **Composer 知识库**：内联模式去掉底部小字；placeholder 缩短；select 弹性宽度；移动端隐藏「知识库」标签。
- **问答类型 Tab**：移动端改横向滚动 + `padding: 0 12px`，四字标签不再贴边。
- **欢迎页**：移动端隐藏副标题、要点减至 2 条单列，给输入区腾高度。

## 2026-07-23 — 扩展页移动端精简与清单可见性

- **已安装 Tab**：移动端默认直接展示技能目录树（不再藏进侧栏抽屉）；点文件后预览走全屏 Drawer，关闭即回到清单。
- **市场 Tab**：列表与详情分离，详情改 Drawer；卡片去掉来源行与次要按钮，腾出列表高度。
- **文案**：扩展页 / Skills / MCP 副标题在移动端隐藏；技能树提示语在 compact 模式省略。

## 2026-07-23 — Composer 知识库收入 + 菜单

- **问题**：智能问答模式下知识库选择器内联在工具栏，移动端独占一行，与模型选择器挤在一起。
- **修复**：`ChatComposerToolbar` 将 `KbScopeSelector` 移入「+」二级菜单「知识库」；工具栏仅保留 `+` 与模型选择；已选库数量/关闭检索在菜单项 badge 展示。

## 2026-07-23 — Skills 移动端：安装按钮与目录展开

- **市场详情**：安装/重新安装按钮通过 `FilePreview#header-extra` 与「预览/源码」同一行，靠右对齐。
- **已安装预览**：关闭 Drawer 不再清空 `selectedKeys`；树组件加载预览时不再 `v-if` 卸载；`expandedKeys` 持久化，默认展开「平台预置」「个人技能」。

## 2026-07-23 — Skills 市场交互与技能包下载

- **市场卡片**：主区域整块可点进详情；已安装只保留「重新安装」按钮（去掉重复「已安装」）；榜单序号改为 `01/02/03` 徽章样式。
- **详情预览**：安装按钮与预览/源码同行靠右；下载按钮 tooltip 标明「下载 SKILL.md」；预览区与 YAML 分隔线间距加大。
- **已安装下载**：新增 `GET /api/skills/fs/package/archive` 打包顶层技能目录；目录右键「下载 ZIP」；文件预览 Drawer 内可「下载技能包 ZIP」；单文件仍用预览栏下载当前文件。

## 2026-07-23 — openspec archive 三个变更的 delta 修复（composer-session-tools / replace-jwt-with-server-sessions / converge-agent-sandbox）

**背景**：三个已实现完成的 change 因 delta spec 与 main spec header/内容不一致，`openspec archive <name> -y` 一直失败，长期挂在 `openspec/changes/`。

**openspec CLI（`@fission-ai/openspec` 1.3.1）合并机制要点**（源码见 `specs-apply.js` / `requirement-blocks.js`）：

- MODIFIED 按「header 精确文本」在 main 中查找旧 requirement 并整块替换（含全部子段落/Scenario），**不是**逐段 diff/merge；写回的 header 必须与查找 key **完全一致**——也就是说 **MODIFIED 不能用来改标题**，改标题必须拆成 `REMOVED`（main 现有精确 header）+ `ADDED`（新 header）。
- 校验器对 requirement 的「必须含 SHALL/MUST」「必须有 Scenario」检查，只看 header 后**第一段**正文（`requirement.text`），第二段及以后的正文会在 `openspec change show --json` 里被丢弃（但不影响 archive 时的 raw 文本合并，因为合并用的是原始文本块而非 parsed JSON）——排查 validate 报错时不要被 `--json --deltas-only` 的 `requirement.text` 截断误导。
- `openspec archive` 会对**整份 rebuilt main spec**做严格校验，会连带暴出与本次 delta 无关的历史遗留问题（如某 requirement 用裸列表当 Scenario、缺 SHALL），必须一起修掉才能通过。
- 无 dry-run 参数；调试用 `node --input-type=module -e "import {Validator} from '.../dist/core/validation/validator.js'; ...validateSpecContent(name, content)"` 直接对着手工拼好的 rebuilt 文本跑校验，比反复 archive 报错更快定位是哪个 requirement（报错的 `requirements.<N>` 是 0-based 的 requirement 序号，需配合 `grep -n "^### Requirement:"` 数序号）。

**处理方式**：
- `composer-session-tools`：requirement 标题已含 SHALL，但正文第一段不含 SHALL → 改写首段带上 SHALL；另有一条 MODIFIED header 文本对不上 main（对应 `agent-fault-operation` 的 MCP 加载描述）→ 改 header 对齐 main 精确文本并把新语义并入正文；顺带修了 `agent-common-qa` 里历史遗留的「会话 SHALL 持久化 kb_collections」缺 Scenario 问题。
- `replace-jwt-with-server-sessions`：delta 本身已不含 REMOVED 段、MODIFIED header 已对齐 main，直接 archive 通过，未做改动。
- `converge-agent-sandbox`（AIO → Docker Exec 收敛，改动最大）：`agent-sandbox`、`container-deployment`、`skills-filesystem` 三个 delta 里几乎所有 MODIFIED header 都是「新 docker-exec 语义标题」对不上 main 的「旧 AIO 语义标题」→ 统一改造为 REMOVED（main 精确旧标题 + Reason/Migration 说明）+ ADDED（delta 原有新标题与正文），只保留标题真正未变的两条（`沙箱环境与密钥`、`沙箱 idle 回收 SHALL 尊重 in-flight Agent`）走 MODIFIED；`agent-runtime-paths` delta 里两条 MODIFIED header 改回 main 精确标题即可（语义变化足够小，不需要拆 REMOVED/ADDED）。

**结果**：三者均已 `archive` 到 `openspec/changes/archive/2026-07-23-*`，main specs（`agent-sandbox`、`agent-runtime-paths`、`container-deployment`、`skills-filesystem`、`user-auth`、`composer-session-tools` 等）已反映实现现状（Cookie session 鉴权；docker-exec 沙箱非 AIO；Composer MCP/Skills 会话级配置）。仍有 3 个与本次无关的历史 spec 校验失败（`kb-chunking`、`kb-document-parse`、`kb-evaluation`），未处理，留给对应领域后续修。

## 2026-07-23 — 搁置 extract-agent-runtime-harness，Delivery 自立

**Why：** 整包迁 `noesis_runtime/` + Profile 注册表 + Harbor 全切影响面过大，短期不解锁多通道；真正需要的是 RunEvent Fan-out 与落库/SSE 解耦。

**How to apply：**
- 主线改为 `unify-run-delivery`（在现有 `agent/` + `qa_service` 上抽 Bus / PersistSink / SseDelivery / ChannelAdapter），**不**等待 harness。
- `extract-agent-runtime-harness` 标 SUPERSEDED 并 archive（`--skip-specs`）；远期若需要可另开 slim「仅 AgentRunService / 评测同入口」。
- `add-agent-user-settings` 仍只做配置面；真收发跟 Delivery。

## 2026-07-23 — Skills 市场分页与移动端列表空白

- **文案**：已安装右键/Drawer 按钮统一为「下载」「删除」；市场详情预览下载按钮简化为「下载」。
- **市场空白**：移动端 flex 高度链断裂导致 `.market-list` 高度为 0；补全 `Extensions` / `market-pane` / `.market-body` 的 `flex` + `min-height`，移动端改为纵向布局可滚动。
- **翻页**：后端 `browse`/`search` 增加 `offset`/`total`（榜单一次拉满 100 条后切片，搜索最多 50）；前端每页 15 条，`NPagination` 翻页；榜单序号按全局排名显示。

## 2026-07-23 — Skills 市场桌面空白与移动端拥挤

- **桌面空白**：`.skills-market` 用 `height:100%` 在 flex 父级下高度为 0；改为 `flex:1; min-height:0`，grid 加 `minmax(0,1fr)` 行/列约束。
- **移动端拥挤**：标题与安装量纵向排列；隐藏列表行内安装按钮（点卡片进 Drawer 安装）；补一行来源省略号；统一每页 12 条（与桌面相同）。
- **断点**：布局样式跟 `isMobile`（≤768px）走 class，不再用 900px 媒体查询与 JS 断点不一致。

## 2026-07-23 — Skills 市场按容器宽度自适应主从布局

- **现象**：平板/窄窗口（侧栏占 280px 后内容区 <720px）仍强制左右分栏，列表挤压重叠、详情错位。
- **做法**：`ResizeObserver` 测市场面板**实际宽度**（非整页视口）；`<720px` 用列表 + Drawer，≥720px 用 `minmax` 流体双栏；卡片标题 `flex-wrap` + `min-width:0` 防溢出。

## 2026-07-23 — Skills 市场窄高度卡片重叠

- **现象**：Web 端压低窗口高度时，列表卡片挤在一起。
- **根因**：`.market-list` 为 flex 列，子项默认 `flex-shrink:1` 被压扁；stacked 模式曾设 `overflow:visible` 无法滚动。
- **修复**：`.market-card { flex-shrink:0 }`；列表/详情区 `overflow-y:auto`；stacked 与 split 均保持 `min-height:0` 滚动链。
