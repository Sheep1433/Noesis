"""Per-session 沙箱容器管理（Docker socket + docker exec）。"""

from __future__ import annotations

import hashlib
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound

from paths import (
    ensure_sandbox_mount_dirs,
    resolve_host_data_dir,
    resolve_skills_host_dir,
)

_SEGMENT_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _validate_segment(value: str, *, kind: str) -> str:
    if not value or not _SEGMENT_RE.match(value):
        raise ValueError(f"非法 {kind}: {value!r}")
    return value


def _cache_key(user_id: str, session_id: str) -> str:
    return f"{user_id}:{session_id}"


def _container_name(user_id: str, session_id: str) -> str:
    digest = hashlib.sha256(f"{user_id}:{session_id}".encode()).hexdigest()[:12]
    return f"noesis-sandbox-{digest}"


def _sandbox_image_build_hint(image: str) -> str:
    return (
        f"沙箱镜像 {image!r} 不存在且拉取失败。"
        "请在仓库根目录执行: "
        f"docker build -t {image} -f deploy/sandbox-slim/Dockerfile ."
    )


@dataclass
class SandboxRecord:
    user_id: str
    session_id: str
    container_id: str
    container_name: str
    runtime: str
    last_used: float = field(default_factory=time.time)
    in_flight: int = 0


class SandboxManager:
    """管理 session 级沙箱容器创建、复用、idle 回收。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, SandboxRecord] = {}
        self._runtime = os.environ.get("SANDBOX_RUNTIME", "docker").strip().lower() or "docker"
        if self._runtime != "docker":
            raise RuntimeError(
                f"非法 SANDBOX_RUNTIME={self._runtime!r}，仅支持 docker（aio 已移除）"
            )
        self._docker_image = os.environ.get(
            "SANDBOX_DOCKER_IMAGE",
            "noesis/sandbox-slim:latest",
        )
        self._docker_network = os.environ.get("SANDBOX_DOCKER_NETWORK", "").strip()
        self._host_data_dir = resolve_host_data_dir()
        self._max_replicas = int(os.environ.get("SANDBOX_MAX_REPLICAS", "20"))
        self._idle_ttl_seconds = int(os.environ.get("SANDBOX_IDLE_TTL_SECONDS", str(72 * 3600)))
        self._sandbox_uid = int(os.environ.get("SANDBOX_UID", "10001"))
        self._sandbox_gid = int(os.environ.get("SANDBOX_GID", "10001"))
        self._mem_limit = os.environ.get("SANDBOX_MEM_LIMIT", "512m").strip() or None
        self._nano_cpus = os.environ.get("SANDBOX_NANO_CPUS", "").strip()
        self._pids_limit = int(os.environ.get("SANDBOX_PIDS_LIMIT", "256"))
        try:
            self._docker = docker.from_env()
        except DockerException as exc:
            raise RuntimeError(f"无法连接 Docker: {exc}") from exc

    def _ensure_image_available(self, image: str) -> None:
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

    def _session_workspace_host(self, user_id: str, session_id: str) -> Path:
        return (
            self._host_data_dir
            / "users"
            / user_id
            / "sessions"
            / session_id
            / "workspace"
        )

    def _personal_skills_host(self, user_id: str) -> Path:
        return self._host_data_dir / "users" / user_id / "skills"

    def _start_container(self, user_id: str, session_id: str) -> SandboxRecord:
        name = _container_name(user_id, session_id)
        workspace_host = self._session_workspace_host(user_id, session_id)
        personal_skills = self._personal_skills_host(user_id)
        skills_host = resolve_skills_host_dir()

        ensure_sandbox_mount_dirs(
            workspace_host,
            personal_skills,
            skills_host,
            uid=self._sandbox_uid,
            gid=self._sandbox_gid,
        )

        # bind source 必须是宿主机真实路径（传给 Docker daemon）
        volumes = {
            str(workspace_host): {"bind": "/workspace", "mode": "rw"},
            str(skills_host): {"bind": "/skills/public", "mode": "ro"},
            str(personal_skills): {"bind": "/skills/personal", "mode": "ro"},
        }

        run_kwargs: dict = {
            "image": self._docker_image,
            "name": name,
            "detach": True,
            "command": ["sleep", "infinity"],
            "volumes": volumes,
            "user": f"{self._sandbox_uid}:{self._sandbox_gid}",
            # 数值 --user 时 Docker 常不设 HOME；缺省会导致 pip --user 写到 /.local
            "environment": {
                "HOME": "/home/sandbox",
                "USER": "sandbox",
                "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            },
            "labels": {
                "noesis.user_id": user_id,
                "noesis.session_id": session_id,
                "noesis.managed": "true",
                "noesis.runtime": "docker",
            },
            "restart_policy": {"Name": "unless-stopped"},
            "cap_drop": ["ALL"],
            "security_opt": ["no-new-privileges:true"],
            "pids_limit": self._pids_limit,
        }
        if self._mem_limit:
            run_kwargs["mem_limit"] = self._mem_limit
        if self._nano_cpus:
            run_kwargs["nano_cpus"] = int(self._nano_cpus)
        if self._docker_network:
            run_kwargs["network"] = self._docker_network

        container = self._run_container(image=self._docker_image, run_kwargs=run_kwargs)
        container.reload()
        if container.status != "running":
            raise RuntimeError(f"沙箱容器启动失败: {name} ({container.status})")
        return SandboxRecord(
            user_id=user_id,
            session_id=session_id,
            container_id=container.id,
            container_name=name,
            runtime="docker",
        )

    def _cleanup_stale_container(self, user_id: str, session_id: str) -> None:
        name = _container_name(user_id, session_id)
        try:
            container = self._docker.containers.get(name)
        except NotFound:
            return
        if container.status in ("running", "created", "restarting"):
            return
        self._stop_and_remove(name)

    def _sync_running(self, user_id: str, session_id: str) -> SandboxRecord | None:
        name = _container_name(user_id, session_id)
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
        return SandboxRecord(
            user_id=user_id,
            session_id=session_id,
            container_id=container.id,
            container_name=name,
            runtime="docker",
        )

    def ensure(self, user_id: str, session_id: str, *, runtime: str | None = None) -> SandboxRecord:
        user_id = _validate_segment(user_id, kind="user_id")
        session_id = _validate_segment(session_id, kind="session_id")
        requested = (runtime or self._runtime).strip().lower()
        if requested != "docker":
            raise ValueError(f"非法 runtime: {requested!r}（仅支持 docker）")

        key = _cache_key(user_id, session_id)
        with self._lock:
            record = self._records.get(key)
            if record is not None:
                record.last_used = time.time()
                return record

            synced = self._sync_running(user_id, session_id)
            if synced is not None:
                self._records[key] = synced
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
                    f"会话沙箱已达上限 sandbox_max_replicas={self._max_replicas}"
                )

            self._cleanup_stale_container(user_id, session_id)
            created = self._start_container(user_id, session_id)
            self._records[key] = created
            return created

    def get_record(self, user_id: str, session_id: str) -> SandboxRecord:
        user_id = _validate_segment(user_id, kind="user_id")
        session_id = _validate_segment(session_id, kind="session_id")
        key = _cache_key(user_id, session_id)
        with self._lock:
            record = self._records.get(key)
            if record is not None:
                record.last_used = time.time()
                return record
            synced = self._sync_running(user_id, session_id)
            if synced is None:
                raise RuntimeError(f"会话沙箱不存在: {user_id}/{session_id}")
            self._records[key] = synced
            synced.last_used = time.time()
            return synced

    def get_container(self, user_id: str, session_id: str):
        record = self.get_record(user_id, session_id)
        try:
            container = self._docker.containers.get(record.container_name)
        except NotFound as exc:
            self._records.pop(_cache_key(user_id, session_id), None)
            raise RuntimeError(f"容器不存在: {record.container_name}") from exc
        container.reload()
        if container.status != "running":
            self._records.pop(_cache_key(user_id, session_id), None)
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

    def destroy(self, user_id: str, session_id: str) -> bool:
        user_id = _validate_segment(user_id, kind="user_id")
        session_id = _validate_segment(session_id, kind="session_id")
        key = _cache_key(user_id, session_id)
        with self._lock:
            self._records.pop(key, None)
            return self._stop_and_remove(_container_name(user_id, session_id))

    def set_in_flight(self, user_id: str, session_id: str, delta: int) -> None:
        user_id = _validate_segment(user_id, kind="user_id")
        session_id = _validate_segment(session_id, kind="session_id")
        key = _cache_key(user_id, session_id)
        with self._lock:
            record = self._records.get(key)
            if record is None:
                synced = self._sync_running(user_id, session_id)
                if synced is None:
                    return
                self._records[key] = synced
                record = synced
            record.in_flight = max(0, record.in_flight + delta)

    def reap_idle(self) -> int:
        """回收 idle 超时且 in-flight==0 的会话沙箱。返回回收数量。"""
        now = time.time()
        removed = 0
        with self._lock:
            candidates = list(self._records.items())
        for key, record in candidates:
            if record.in_flight > 0:
                continue
            if now - record.last_used < self._idle_ttl_seconds:
                continue
            if self.destroy(record.user_id, record.session_id):
                removed += 1
        return removed

    def shutdown_all(self) -> None:
        with self._lock:
            records = list(self._records.values())
        for record in records:
            self.destroy(record.user_id, record.session_id)
