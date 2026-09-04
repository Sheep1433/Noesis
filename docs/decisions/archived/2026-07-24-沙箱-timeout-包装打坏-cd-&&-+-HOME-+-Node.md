# 决策：沙箱 timeout 包装打坏 `cd &&` + HOME + Node

状态：implemented
日期：2026-07-24
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

**Why：** Langfuse/DB 轨迹里 Word 轮出现 `timeout: failed to run command 'cd'`；同轮还有无 node、pip 写 `/.local` Permission denied。

**根因：**
1. `docker_exec.exec_command` 把用户命令拼成 `timeout N <cmd>`。当 cmd 为 `cd /workspace && …` 时，bash 解析成 `(timeout N cd /workspace) && …`，GNU `timeout` 去 exec builtin `cd` → No such file。
2. 容器 `user: uid:gid` 数值用户时 Docker 常不注入 passwd 家目录，`HOME` 空/`/` → pip `--user` 写 `/.local`。
3. 旧 slim 无 Node，与 `docx` 等 npm skill 假设冲突。

**长期是否合理：**
| 改动 | 结论 |
|------|------|
| `timeout … bash -lc <cmd>` | ✅ 正确；timeout 只应包解释器，不该直接吃 shell 语法 |
| 显式 `HOME=/home/sandbox`（run + exec） | ✅ 正确；数值 `--user` 下的标准做法 |
| slim 装 `nodejs`/`npm` + `NPM_CONFIG_PREFIX` 到家目录 | ✅ 与常见 skill 对齐；代价是镜像变大 |
| `python3-venv` | ✅ 长期 pip 应走 workspace/venv，少用 `--break-system-packages` |
| 靠 `--break-system-packages` / 写系统 site-packages | ❌ 不作为主路径 |

**How to apply：**
- timeout：`["timeout", "--signal=TERM", N, "/bin/bash", "-lc", command]`
- 容器与 exec：`HOME=/home/sandbox`
- 镜像：`nodejs` `npm` `python3-venv`；部署后 rebuild slim + 重建 runner/session 容器
- 回归：`deploy/sandbox-runner/test_docker_exec.py`
