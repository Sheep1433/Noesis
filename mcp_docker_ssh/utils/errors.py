from typing import Any, Dict, Optional

from fastmcp.exceptions import ToolError


class MCPError(ToolError):
    """Base error for MCP tools. Raised to signal tool failure at the MCP protocol level."""

    error_code: str = "INTERNAL_ERROR"
    message: str = "Internal error"
    retryable: bool = False

    def __init__(self, message: str | None = None, *, details: Optional[object] = None):
        self.details = details
        self.resolved_message = message or self.message
        super().__init__(f"[{self.error_code}] {self.resolved_message}")

    def to_dict(self) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            "error_code": self.error_code,
            "message": self.resolved_message,
            "retryable": self.retryable,
        }
        if self.details is not None:
            result["details"] = self.details
        return result


class InternalError(MCPError):
    error_code = "INTERNAL_ERROR"
    message = "Internal error"
    retryable = False


class SSHAuthRequiredError(MCPError):
    error_code = "SSH_AUTH_REQUIRED"
    message = "SSH authentication required (key auth failed, password may be provided)"
    retryable = True


class SSHAuthFailedError(MCPError):
    error_code = "SSH_AUTH_FAILED"
    message = "SSH authentication failed"
    retryable = False


class SSHConnectionFailedError(MCPError):
    error_code = "SSH_CONNECTION_FAILED"
    message = "Failed to connect to SSH server"
    retryable = True


class CommandTimeoutError(MCPError):
    error_code = "COMMAND_TIMEOUT"
    message = "Command execution timed out"
    retryable = True


class CommandExecutionError(MCPError):
    error_code = "COMMAND_EXECUTION_FAILED"
    message = "Command execution failed"
    retryable = False


class PathNotFoundError(MCPError):
    error_code = "PATH_NOT_FOUND"
    message = "File or directory not found"
    retryable = False


class ContainerNotRunningError(MCPError):
    """Internal error: container not running. Signals transparent retry, not returned to client."""

    error_code = "CONTAINER_NOT_RUNNING"
    message = "Container is not running"
    retryable = True
