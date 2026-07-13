"""Per-user 沙箱容器管理（Docker socket，docker exec / 可选 AIO HTTP）。"""

from __future__ import annotations

import hashlib
import os
import re
import socket
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import docker
import httpx
from docker.errors import APIError, DockerException, ImageNotFound, NotFound

from paths import (
    ensure_sandbox_mount_readable,
    resolve_host_data_dir,
    resolve_skills_host_dir,
)

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_user_id(user_id: str) -> str:
    if not user_id or not _SEGMENT_RE.match(user_id):
        raise ValueError(f"非法 user_id: {user_id!r}")
    return user_id


def _container_name(user_id: str) -> str:
    digest = hashlib.sha256(user_id.encode()).hexdigest()[:12]
    return f"noesis-sandbox-{digest}"


def _sandbox_image_build_hint(image: str) -> str:
    return (
        f"沙箱镜像 {image!r} 不存在且拉取失败。"
        "请在仓库根目录执行: "
        f"docker build -t {image} -f deploy/sandbox-slim/Dockerfile ."
    )


def _public_host() -> str:
    """backend 访问 AIO 时使用的主机名（本地 dev 默认 127.0.0.1）。"""
    return os.environ.get("SANDBOX_PUBLIC_HOST", "127.0.0.1").strip() or "127.0.0.1"


def _host_port_base() -> int:
    return int(os.environ.get("SANDBOX_HOST_PORT_BASE", "18080"))


def _find_free_host_port(*, start: int | None = None) -> int:
    base = start if start is not None else _host_port_base()
    for port in range(base, base + 2000):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                continue
            return port
    raise RuntimeError(f"无可用宿主机端口（起始于 {base}）")


def _extract_host_port(container: docker.models.containers.Container, container_port: int) -> int | None:
    container.reload()
    labels = container.labels or {}
    label_port = labels.get("noesis.host_port")
    if label_port:
        try:
            return int(label_port)
        except ValueError:
            pass
    ports = (container.attrs.get("NetworkSettings") or {}).get("Ports") or {}
    bindings = ports.get(f"{container_port}/tcp") or []
    if bindings and bindings[0].get("HostPort"):
        return int(bindings[0]["HostPort"])
    return None


def _published_base_url(host_port: int) -> str:
    return f"http://{_public_host()}:{host_port}"


@dataclass
class SandboxRecord:
    user_id: str
    container_id: str
    container_name: str
    runtime: str
    base_url: str | None = None
    last_used: float = field(default_factory=time.time)
    in_flight: int = 0


class SandboxManager:
    """管理用户级 AIO 容器创建、复用、idle 回收。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, SandboxRecord] = {}
        self._runtime = os.environ.get("SANDBOX_RUNTIME", "docker").strip().lower() or "docker"
        if self._runtime not in ("docker", "aio"):
            raise RuntimeError(f"非法 SANDBOX_RUNTIME={self._runtime!r}，仅支持 docker | aio")
        self._docker_image = os.environ.get(
            "SANDBOX_DOCKER_IMAGE",
            os.environ.get("SANDBOX_AIO_IMAGE", "noesis/sandbox-slim:latest"),
        )
        self._aio_image = os.environ.get(
            "SANDBOX_AIO_IMAGE", "ghcr.io/agent-infra/sandbox:latest"
        )
        self._aio_port = int(os.environ.get("SANDBOX_AIO_PORT", "8080"))
        self._docker_network = os.environ.get("SANDBOX_DOCKER_NETWORK", "").strip()
        self._host_port_base = _host_port_base()
        self._host_data_dir = resolve_host_data_dir()
        self._max_replicas = int(os.environ.get("SANDBOX_MAX_REPLICAS", "20"))
        self._idle_ttl_seconds = int(os.environ.get("SANDBOX_IDLE_TTL_SECONDS", str(72 * 3600)))
        self._headless = os.environ.get("SANDBOX_HEADLESS", "1")
        self._chrome_path = os.environ.get(
            "URL_CHROME_PATH", "/usr/bin/google-chrome-stable"
        )
        try:
            self._docker = docker.from_env()
        except DockerException as exc:
            raise RuntimeError(f"无法连接 Docker: {exc}") from exc

    def _ensure_image_available(self, image: str) -> None:
        """本地无镜像时不隐式 pull（避免 Docker Hub 超时变成 HTTP 500）。"""
        try:
            self._docker.images.get(image)
        except ImageNotFound as exc:
            raise RuntimeError(_sandbox_image_build_hint(image)) from exc

    def _run_container(self, *, image: str, run_kwargs: dict):
        self._ensure_image_available(image)
        try:
            return self._docker.containers.run(**run_kwargs)
        except APIError as exc:
            hint = _sandbox_image_build_hint(image)
            if "pull" in str(exc).lower() or "not found" in str(exc).lower():
                raise RuntimeError(hint) from exc
            raise RuntimeError(f"启动沙箱容器失败 ({image}): {exc}") from exc

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

    def _start_container(self, user_id: str, *, runtime: str) -> SandboxRecord:
        if runtime not in ("docker", "aio"):
            raise ValueError(f"非法 runtime: {runtime!r}")
        name = _container_name(user_id)
        user_ws = self._user_workspace_host(user_id)
        user_ws.mkdir(parents=True, exist_ok=True)
        ensure_sandbox_mount_readable(user_ws)
        user_skills = user_ws / "skills"
        user_skills.mkdir(parents=True, exist_ok=True)
        ensure_sandbox_mount_readable(user_skills, recursive=True)

        skills_host = resolve_skills_host_dir()
        ensure_sandbox_mount_readable(skills_host, recursive=True)

        volumes = {
            str(user_ws): {"bind": "/workspace", "mode": "rw"},
            str(skills_host): {"bind": "/skills", "mode": "ro"},
        }

        run_kwargs: dict = {
            "name": name,
            "detach": True,
            "volumes": volumes,
            "labels": {
                "noesis.user_id": user_id,
                "noesis.managed": "true",
                "noesis.runtime": runtime,
            },
            "restart_policy": {"Name": "unless-stopped"},
        }
        if self._docker_network:
            run_kwargs["network"] = self._docker_network

        if runtime == "aio":
            host_port = _find_free_host_port(start=self._host_port_base)
            run_kwargs.update(
                {
                    "image": self._aio_image,
                    "ports": {f"{self._aio_port}/tcp": host_port},
                    "environment": {
                        "SANDBOX_HEADLESS": self._headless,
                        "URL_CHROME_PATH": self._chrome_path,
                    },
                    "labels": {
                        **run_kwargs["labels"],
                        "noesis.host_port": str(host_port),
                    },
                }
            )
            container = self._run_container(image=self._aio_image, run_kwargs=run_kwargs)
            base_url = _published_base_url(host_port)
            self._wait_aio_ready(base_url)
            return SandboxRecord(
                user_id=user_id,
                container_id=container.id,
                container_name=name,
                runtime="aio",
                base_url=base_url,
            )

        run_kwargs.update(
            {
                "image": self._docker_image,
                "command": ["sleep", "infinity"],
            }
        )
        container = self._run_container(image=self._docker_image, run_kwargs=run_kwargs)
        container.reload()
        if container.status != "running":
            raise RuntimeError(f"沙箱容器启动失败: {name} ({container.status})")
        return SandboxRecord(
            user_id=user_id,
            container_id=container.id,
            container_name=name,
            runtime="docker",
            base_url=None,
        )

    def _cleanup_stale_container(self, user_id: str) -> None:
        """删除已停止/异常容器，避免 compose 重部署后同名创建 409。"""
        name = _container_name(user_id)
        try:
            container = self._docker.containers.get(name)
        except NotFound:
            return
        if container.status in ("running", "created", "restarting"):
            return
        self._stop_and_remove(name)

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
        labels = container.labels or {}
        runtime = labels.get("noesis.runtime", self._runtime)
        base_url: str | None = None
        if runtime == "aio":
            host_port = _extract_host_port(container, self._aio_port)
            if host_port is None:
                return None
            base_url = _published_base_url(host_port)
        return SandboxRecord(
            user_id=user_id,
            container_id=container.id,
            container_name=name,
            runtime=runtime,
            base_url=base_url,
        )

    def ensure(self, user_id: str, *, runtime: str | None = None) -> SandboxRecord:
        user_id = _validate_user_id(user_id)
        requested = (runtime or self._runtime).strip().lower()
        if requested not in ("docker", "aio"):
            raise ValueError(f"非法 runtime: {requested!r}")

        with self._lock:
            record = self._records.get(user_id)
            if record is not None:
                if record.runtime != requested:
                    self._records.pop(user_id, None)
                    self._stop_and_remove(_container_name(user_id))
                else:
                    record.last_used = time.time()
                    return record

            synced = self._sync_running(user_id)
            if synced is not None:
                if synced.runtime != requested:
                    self._stop_and_remove(synced.container_name)
                else:
                    self._records[user_id] = synced
                    synced.last_used = time.time()
                    return synced

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

            self._cleanup_stale_container(user_id)
            created = self._start_container(user_id, runtime=requested)
            self._records[user_id] = created
            return created

    def get_record(self, user_id: str) -> SandboxRecord:
        user_id = _validate_user_id(user_id)
        with self._lock:
            record = self._records.get(user_id)
            if record is not None:
                record.last_used = time.time()
                return record
            synced = self._sync_running(user_id)
            if synced is None:
                raise RuntimeError(f"用户沙箱不存在: {user_id}")
            self._records[user_id] = synced
            synced.last_used = time.time()
            return synced

    def get_container(self, user_id: str):
        record = self.get_record(user_id)
        try:
            container = self._docker.containers.get(record.container_name)
        except NotFound as exc:
            raise RuntimeError(f"容器不存在: {record.container_name}") from exc
        container.reload()
        if container.status != "running":
            raise RuntimeError(
                f"容器未运行: {record.container_name} ({container.status})"
            )
        return container

    def _stop_and_remove(self, name: str) -> bool:
        try:
            container = self._docker.containers.get(name)
        except NotFound:
            return False
        container.stop(timeout=15)
        container.remove(force=True)
        return True

    def destroy(self, user_id: str) -> bool:
        user_id = _validate_user_id(user_id)
        with self._lock:
            self._records.pop(user_id, None)
            return self._stop_and_remove(_container_name(user_id))

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
