# 决策：RevisableSkillsMiddleware 缺 config 参数

状态：implemented
日期：2026-07-21
迁移：自 docs/NOTES.md 机械拆分（2026-09-01），正文零改写

---

- **现象**：超级智能体启动报 `SkillsMiddleware.abefore_agent() missing 1 required positional argument: 'config'`。
- **根因**：deepagents `SkillsMiddleware.(a)before_agent` 已增加 `config: RunnableConfig`；本地子类覆盖仍按旧签名 `(state, runtime)` 调用 `super()`。
- **修复**：`revisable_skills_middleware.py` 同步接收并转发 `config`。
