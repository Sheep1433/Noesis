# ERB 官方判分脚本（移植版）

来源：[onyx-dot-app/EnterpriseRAG-Bench](https://github.com/onyx-dot-app/EnterpriseRAG-Bench)
commit `d36685e273713975ee20299bbf1ab64165575b3c`（2026-05-07 arXiv 版），MIT License（见 LICENSE）。

判分 prompt 与四项指标算法（Correctness / Completeness / Document Recall / Invalid Extra Documents）
逐行取自官方；相对官方的三处本地化：

1. **LLM 传输**（`src/llm/openai_llm.py`）：官方客户端写死 api.openai.com + Responses API；
   增加 `LLM_BASE_URL` 环境变量支持自定义 OpenAI 兼容网关，无工具调用走 Chat Completions。
2. **依赖裁剪**：只保留判分依赖闭包（21 个文件）；数据生成管线（自动对话器、Agent 工具框架、
   出题 prompt 主体）不在本仓库用途内。`src/utils/__init__.py` 的桶式 import 改为空，
   判分代码全部直接从子模块 import，不受影响。
3. **金标子集**：`questions.jsonl`（248 题金标：erb211 正样本 + 17 冲突陷阱 + 20 拒答
   负样本）取自官方数据子集。语料 JSON（`generated_data/sources/`，532 篇）**不入版本库**
   （生成数据缓存，gitignore），从官方仓库 commit `d36685e` 的
   `generated_data/sources/` 按需拷贝对应 dsid 文件即可；全量 50 万篇见官方 Releases。

## 运行

```bash
cd backend/evals/agent/rag/erb_scorer && \
LLM_PROVIDER=openai LLM_API_KEY=<key> LLM_BASE_URL=<gateway> LLM_MODEL_NAME=<裁判模型> \
uv run --with openai --with 'pydantic[email]' --with tiktoken --with pyyaml \
  --with python-dotenv --with pyarrow \
python -m src.scripts.answer_evaluation.metrics_based_eval \
  --answers-file <run 目录>/erb_answers.jsonl --parallelism 6
```

输入格式（`to_erb.py` 产出）：`{"question_id", "answer", "document_ids"}` 每行一条。
判分前官方会先剥除回答中的引用标记；`--no-correction` 表示按原始金标判分
（跳过官方三裁判共识纠错，内部回归口径）。裁判模型随结果声明（官方论文口径 GPT-5.4）。

`generated_data/uuid_index.json` 为首次运行自动构建的语料索引缓存（gitignore）。
