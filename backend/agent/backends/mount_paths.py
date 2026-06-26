"""Agent 与 AIO 容器挂载路径常量。"""

# 容器内物理挂载（AIO bind mount）
EXTENSIONS_SKILLS_CONTAINER_PREFIX = "/skills"
CUSTOM_SKILLS_CONTAINER_PREFIX = "/workspace/skills"

# Agent 虚拟路径（CompositeBackend route + SkillsMiddleware sources）
AGENT_EXTENSIONS_SKILLS_ROUTE = "/skills/extensions/"
AGENT_CUSTOM_SKILLS_ROUTE = "/skills/custom/"

# 兼容旧引用（容器前缀别名）
PLATFORM_SKILLS_CONTAINER_PREFIX = EXTENSIONS_SKILLS_CONTAINER_PREFIX
USER_SKILLS_CONTAINER_PREFIX = CUSTOM_SKILLS_CONTAINER_PREFIX
