from __future__ import annotations

import asyncio
import hashlib
import threading
import time
from pathlib import Path
from typing import Optional

import docker
from docker.errors import NotFound

from config import get_config, resolved_container_ssh_dir
from utils.errors import CommandTimeoutError, ContainerNotRunningError, InternalError
from utils.output_handler import CommandResult, OutputHandler


class DockerManager:
    _instance: Optional["DockerManager"] = None
    _lock = threading.Lock()

    def __new__(cls) -> "DockerManager":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._client: Optional[docker.DockerClient] = None
        self._container_id: Optional[str] = None
        self._container_name: Optional[str] = None
        self._created_at: Optional[float] = None
        self._output_handler = OutputHandler()
        self._initialized = True

    @property
    def client(self) -> docker.DockerClient:
        if self._client is None:
            self._client = docker.from_env()
        return self._client

    def _get_container_name(self) -> str:
        cfg = get_config()
        ssh_dir = cfg.container.ssh_dir
        image = cfg.docker.image
        hash_input = f"{ssh_dir}:{image}".encode("utf-8")
        config_hash = hashlib.sha256(hash_input).hexdigest()[:12]
        return f"mcp-sandbox-{config_hash}"

    def _create_container(self) -> None:
        cfg = get_config()
        image_name = cfg.docker.image
        name = self._get_container_name()

        # Verify image exists
        try:
            self.client.images.get(image_name)
        except NotFound as e:
            raise InternalError(
                f"Docker image {image_name} not found. Please pull it first: docker pull {image_name}"
            ) from e

        # Try to reuse existing container
        try:
            existing = self.client.containers.get(name)
            if existing.status == "running":
                self._container_id = existing.id
                self._container_name = name
                self._created_at = time.time()
                return
            # If exists but not running, start it
            existing.start()
            self._container_id = existing.id
            self._container_name = name
            self._created_at = time.time()
            return
        except NotFound:
            pass

        host_ssh = Path(resolved_container_ssh_dir(cfg))
        container_ssh = resolved_container_ssh_dir(cfg)
        binds = {}
        if host_ssh.exists():
            binds[str(host_ssh)] = {"bind": container_ssh, "mode": "ro"}

        container = self.client.containers.run(
            image=cfg.docker.image,
            command="sleep infinity",
            detach=True,
            name=name,
            remove=False,
            read_only=False,
            security_opt=["seccomp=unconfined"],
            **({"volumes": binds} if binds else {}),
        )
        self._container_id = container.id
        self._container_name = name
        self._created_at = time.time()

    def _rebuild_container(self) -> None:
        name = self._get_container_name()
        try:
            container = self.client.containers.get(name)
            container.stop(timeout=5)
            container.remove(force=True)
        except NotFound:
            pass
        self._container_id = None
        self._container_name = None
        self._created_at = None
        self._create_container()

    def ensure_container_running(self) -> str:
        """Ensure the sandbox container is running. Thread-safe via mutex on container creation."""
        cfg = get_config()
        name = self._get_container_name()

        # Fast path: check without lock if already tracked and healthy
        if self._container_id:
            try:
                container = self.client.containers.get(name)
                if container.status == "running":
                    # Check lifetime
                    if self._created_at and (time.time() - self._created_at) > cfg.container.max_lifetime_seconds:
                        self._rebuild_container()
                    return self._container_id
            except NotFound:
                pass

        # Slow path: acquire lock for container creation
        with self._lock:
            # Double-check after acquiring lock
            if self._container_id:
                try:
                    container = self.client.containers.get(name)
                    if container.status == "running":
                        if self._created_at and (time.time() - self._created_at) > cfg.container.max_lifetime_seconds:
                            self._rebuild_container()
                        return self._container_id
                except NotFound:
                    pass

            self._create_container()
            return self._container_id  # type: ignore

    def cleanup(self) -> None:
        """Stop and remove the container."""
        with self._lock:
            if self._container_id:
                try:
                    container = self.client.containers.get(self._container_id)
                    container.stop(timeout=5)
                    container.remove(force=True)
                except Exception:
                    pass
                finally:
                    self._container_id = None
                    self._container_name = None
                    self._created_at = None

    def exec_in_container(self, cmd: list[str], timeout: Optional[int] = None) -> tuple[int, str, str]:
        """Execute a command inside the sandbox container. Returns (exit_code, stdout, stderr)."""
        if timeout is None:
            timeout = get_config().execution.timeout

        try:
            container_id = self.ensure_container_running()
            container = self.client.containers.get(container_id)
        except NotFound as e:
            raise ContainerNotRunningError(str(e)) from e
        except docker.errors.APIError as e:
            raise ContainerNotRunningError(str(e)) from e

        try:
            if timeout:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    async_result = loop.run_in_executor(None, lambda: container.exec_run(cmd, demux=True))
                    exit_code, output = loop.run_until_complete(asyncio.wait_for(async_result, timeout=timeout))
                except asyncio.TimeoutError as e:
                    raise CommandTimeoutError(f"Command timed out after {timeout}s") from e
                finally:
                    loop.close()
            else:
                exit_code, output = container.exec_run(cmd, demux=True)
            stdout, stderr = output if isinstance(output, tuple) else (output, b"")
            stdout_str = (stdout or b"").decode("utf-8", errors="replace")
            stderr_str = (stderr or b"").decode("utf-8", errors="replace")

            result = self._output_handler.truncate_result(
                CommandResult(stdout=stdout_str, stderr=stderr_str, exit_code=exit_code)
            )
            return result.exit_code, result.stdout, result.stderr
        except CommandTimeoutError:
            raise
        except docker.errors.APIError as e:
            raise ContainerNotRunningError(f"exec_run failed: {e}") from e
