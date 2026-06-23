"""Per-user AIO 沙箱容器管理（Docker socket）。"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import docker
import httpx
from docker.errors import DockerException, NotFound

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_user_id(user_id: str) -> str:
    if not user_id or not _SEGMENT_RE.match(user_id):
        raise ValueError(f"非法 user_id: {user_id!r}")
    return user_id


def _container_name(user_id: str) -> str:
    digest = hashlib.sha256(user_id.encode()).hexdigest()[:12]
    return f"noesis-aio-{digest}"


@dataclass
class SandboxRecord:
    user_id: str
    container_id: str
    base_url: str
    last_used: float = field(default_factory=time.time)
    in_flight: int = 0


class SandboxManager:
    """管理用户级 AIO 容器创建、复用、idle 回收。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, SandboxRecord] = {}
        self._aio_image = os.environ.get(
            "SANDBOX_AIO_IMAGE", "ghcr.io/agent-infra/sandbox:latest"
        )
        self._aio_port = int(os.environ.get("SANDBOX_AIO_PORT", "8080"))
        self._docker_network = os.environ.get("SANDBOX_DOCKER_NETWORK", "noesis_default")
        self._host_data_dir = Path(
            os.environ.get("NOESIS_HOST_DATA_DIR", "/data/noesis")
        ).resolve()
        self._skills_host_dir = Path(
            os.environ.get("SANDBOX_SKILLS_HOST_DIR", "/data/skills")
        ).resolve()
        self._max_replicas = int(os.environ.get("SANDBOX_MAX_REPLICAS", "20"))
        self._idle_ttl_seconds = int(os.environ.get("SANDBOX_IDLE_TTL_SECONDS", "1800"))
        self._headless = os.environ.get("SANDBOX_HEADLESS", "1")
        self._chrome_path = os.environ.get(
            "URL_CHROME_PATH", "/usr/bin/google-chrome-stable"
        )
        try:
            self._docker = docker.from_env()
        except DockerException as exc:
            raise RuntimeError(f"无法连接 Docker: {exc}") from exc

    def _user_workspace_host(self, user_id: str) -> Path:
        return self._host_data_dir / "users" / user_id

    def _wait_aio_ready(self, base_url: str, *, timeout: float = 90.0) -> None:
        deadline = time.time() + timeout
        last_error: str | None = None
        with httpx.Client(timeout=5.0) as client:
            while time.time() < deadline:
                try:
                    resp = client.get(f"{base_url.rstrip('/')}/v1/sandbox")
                    if resp.status_code < 500:
                        return
                    last_error = f"HTTP {resp.status_code}"
                except httpx.HTTPError as exc:
                    last_error = str(exc)
                time.sleep(1.0)
        raise RuntimeError(f"AIO 沙箱未就绪 ({base_url}): {last_error}")

    def _start_container(self, user_id: str) -> SandboxRecord:
        name = _container_name(user_id)
        user_ws = self._user_workspace_host(user_id)
        user_ws.mkdir(parents=True, exist_ok=True)

        volumes = {
            str(user_ws): {"bind": "/workspace", "mode": "rw"},
        }
        if self._skills_host_dir.is_dir():
            volumes[str(self._skills_host_dir)] = {"bind": "/skills", "mode": "ro"}

        container = self._docker.containers.run(
            image=self._aio_image,
            name=name,
            detach=True,
            network=self._docker_network,
            volumes=volumes,
            environment={
                "SANDBOX_HEADLESS": self._headless,
                "URL_CHROME_PATH": self._chrome_path,
            },
            labels={"noesis.user_id": user_id, "noesis.managed": "true"},
            restart_policy={"Name": "unless-stopped"},
        )
        base_url = f"http://{name}:{self._aio_port}"
        self._wait_aio_ready(base_url)
        return SandboxRecord(
            user_id=user_id,
            container_id=container.id,
            base_url=base_url,
        )

    def _sync_running(self, user_id: str) -> SandboxRecord | None:
        name = _container_name(user_id)
        try:
            container = self._docker.containers.get(name)
        except NotFound:
            return None
        if container.status not in ("running", "created", "restarting"):
            return None
        if container.status != "running":
            container.reload()
            if container.status != "running":
                return None
        base_url = f"http://{name}:{self._aio_port}"
        return SandboxRecord(
            user_id=user_id,
            container_id=container.id,
            base_url=base_url,
        )

    def ensure(self, user_id: str) -> str:
        user_id = _validate_user_id(user_id)
        with self._lock:
            record = self._records.get(user_id)
            if record is not None:
                record.last_used = time.time()
                return record.base_url

            synced = self._sync_running(user_id)
            if synced is not None:
                self._records[user_id] = synced
                synced.last_used = time.time()
                return synced.base_url

            active = len(self._records) or len(
                [
                    c
                    for c in self._docker.containers.list(
                        all=True, filters={"label": "noesis.managed=true"}
                    )
                    if c.status == "running"
                ]
            )
            if active >= self._max_replicas:
                raise RuntimeError(
                    f"用户沙箱已达上限 sandbox_max_replicas={self._max_replicas}"
                )

            created = self._start_container(user_id)
            self._records[user_id] = created
            return created.base_url

    def destroy(self, user_id: str) -> bool:
        user_id = _validate_user_id(user_id)
        with self._lock:
            self._records.pop(user_id, None)
            name = _container_name(user_id)
            try:
                container = self._docker.containers.get(name)
            except NotFound:
                return False
            container.stop(timeout=15)
            container.remove(force=True)
            return True

    def set_in_flight(self, user_id: str, delta: int) -> None:
        user_id = _validate_user_id(user_id)
        with self._lock:
            record = self._records.get(user_id)
            if record is None:
                synced = self._sync_running(user_id)
                if synced is None:
                    return
                self._records[user_id] = synced
                record = synced
            record.in_flight = max(0, record.in_flight + delta)

    def reap_idle(self) -> int:
        """回收 idle 超时且 in-flight==0 的用户沙箱。返回回收数量。"""
        now = time.time()
        removed = 0
        with self._lock:
            candidates = list(self._records.items())
        for user_id, record in candidates:
            if record.in_flight > 0:
                continue
            if now - record.last_used < self._idle_ttl_seconds:
                continue
            if self.destroy(user_id):
                removed += 1
        return removed

    def shutdown_all(self) -> None:
        with self._lock:
            user_ids = list(self._records.keys())
        for user_id in user_ids:
            self.destroy(user_id)
