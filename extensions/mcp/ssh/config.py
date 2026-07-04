from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

# Claude Code aligned defaults (see 文件系统工具对比分析.md)
VCS_DIRECTORIES_TO_EXCLUDE = (
    ".git",
    ".svn",
    ".hg",
    ".bzr",
    ".jj",
    ".sl",
)


@dataclass
class ToolLimitsConfig:
    """Per-tool truncation limits aligned with Claude Code."""

    grep_head_limit: int = 250
    grep_max_result_chars: int = 20_000
    glob_max_files: int = 100
    glob_max_result_chars: int = 100_000
    bash_max_result_chars: int = 30_000
    bash_default_timeout_ms: int = 120_000
    read_max_line_chars: int = 5000
    read_default_limit: int = 2000


@dataclass
class ExecutionConfig:
    timeout: int = 30
    connect_timeout: int = 10
    max_output_size: int = 1048576
    max_output_lines: int = 5000


@dataclass
class SSHConfig:
    default_user: str = "root"
    default_port: int = 22
    ssh_dir: str = "~/.ssh"


@dataclass
class MCPConfig:
    tool_limits: ToolLimitsConfig = field(default_factory=ToolLimitsConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    ssh: SSHConfig = field(default_factory=SSHConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> MCPConfig:
        path = Path(path)
        if not path.exists():
            return cls()

        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}

        return cls(
            tool_limits=ToolLimitsConfig(**raw.get("tool_limits", {})),
            execution=ExecutionConfig(**raw.get("execution", {})),
            ssh=SSHConfig(**raw.get("ssh", {})),
        )


def _apply_env_overrides(cfg: MCPConfig) -> MCPConfig:
    """Override config values from environment variables."""
    if os.environ.get("MCP_SSH_DEFAULT_USER"):
        cfg.ssh.default_user = os.environ["MCP_SSH_DEFAULT_USER"]
    if os.environ.get("MCP_SSH_DEFAULT_PORT"):
        cfg.ssh.default_port = int(os.environ["MCP_SSH_DEFAULT_PORT"])
    if os.environ.get("MCP_SSH_DIR"):
        cfg.ssh.ssh_dir = os.environ["MCP_SSH_DIR"]
    if os.environ.get("MCP_EXECUTION_TIMEOUT"):
        cfg.execution.timeout = int(os.environ["MCP_EXECUTION_TIMEOUT"])
    if os.environ.get("BASH_DEFAULT_TIMEOUT_MS"):
        cfg.tool_limits.bash_default_timeout_ms = int(os.environ["BASH_DEFAULT_TIMEOUT_MS"])
    return cfg


def resolved_ssh_dir(cfg: MCPConfig | None = None) -> str:
    """Expand ~ in MCP host SSH key directory."""
    cfg = cfg or get_config()
    return os.path.expanduser(cfg.ssh.ssh_dir)


def get_config() -> MCPConfig:
    """Load MCP config from config.yaml, then apply environment variable overrides."""
    cfg_path = Path(__file__).parent / "config.yaml"
    return _apply_env_overrides(MCPConfig.from_yaml(cfg_path))
