"""Agent 与沙箱容器挂载路径常量。"""

# 容器内物理挂载（session workspace + 双 Skills）
WORKSPACE_CONTAINER_PREFIX = "/workspace"
PUBLIC_SKILLS_CONTAINER_PREFIX = "/skills/public"
PERSONAL_SKILLS_CONTAINER_PREFIX = "/skills/personal"

# Agent 虚拟路径（CompositeBackend route + SkillsMiddleware sources）
AGENT_PUBLIC_SKILLS_ROUTE = "/skills/public/"
AGENT_PERSONAL_SKILLS_ROUTE = "/skills/personal/"

# 过渡期 filesystem 别名（映射到 public/personal；SHALL NOT 用于 Shell rewrite）
AGENT_EXTENSIONS_SKILLS_ROUTE = "/skills/extensions/"
AGENT_CUSTOM_SKILLS_ROUTE = "/skills/custom/"

# 用户级跨会话记忆（Agent virtual `/memory/`；不经沙箱 Shell 挂载）
AGENT_MEMORY_ROUTE = "/memory/"
AGENT_MEMORY_AGENTS_FILE = "/memory/AGENTS.md"
AGENT_MEMORY_USER_FILE = "/memory/USER.md"

# 兼容旧名
EXTENSIONS_SKILLS_CONTAINER_PREFIX = PUBLIC_SKILLS_CONTAINER_PREFIX
CUSTOM_SKILLS_CONTAINER_PREFIX = PERSONAL_SKILLS_CONTAINER_PREFIX
USER_DATA_CONTAINER_PREFIX = WORKSPACE_CONTAINER_PREFIX
AGENT_SKILLS_INDEX_ROUTE = "/skills/"
