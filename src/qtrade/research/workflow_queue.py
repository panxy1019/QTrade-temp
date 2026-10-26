"""Append-only private Git queue; fixed destination, pinned SSH host identity."""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from qtrade.research.workflow import canonical

QUEUE_URL = "ssh://git@ssh.github.com:443/panxy1019/QTrade-research-queue.git"
IDENTITY = b"QTrade Research <research@qtrade.invalid>"


def seal(value: dict[str, Any], secret: str) -> dict[str, Any]:
    if len(secret) < 32:
        raise ValueError("QUEUE_SIGNING_KEY_REQUIRED")
    return {**value, "queue_signature": hmac.new(
        secret.encode(), canonical(value), hashlib.sha256
    ).hexdigest()}


def unseal(value: dict[str, Any], secret: str) -> dict[str, Any]:
    payload = {k: v for k, v in value.items() if k != "queue_signature"}
    if not hmac.compare_digest(
        str(value.get("queue_signature", "")), seal(payload, secret)["queue_signature"]
    ):
        raise ValueError("QUEUE_SIGNATURE_MISMATCH")
    return payload


class GitQueue:
    """Each append uses a fresh checkout and a non-force push (CAS at Git ref)."""

    def __init__(self, key: str, known_host: str) -> None:
        if not key or not known_host.startswith("ssh-ed25519 "):
            raise ValueError("QUEUE_CREDENTIALS_NOT_CONFIGURED")
        self._key = key
        self._known_host = known_host

    @classmethod
    def from_environment(cls) -> GitQueue:
        return cls(
            os.environ.get("QTRADE_RESEARCH_QUEUE_KEY", "").replace("\\n", "\n"),
            os.environ.get("QTRADE_RESEARCH_GITHUB_HOST_KEY", ""),
        )

    def _vendor(self) -> Any:
        import paramiko  # type: ignore[import-untyped]
        from dulwich.contrib.paramiko_vendor import _ParamikoWrapper

        private = paramiko.Ed25519Key.from_private_key(io.StringIO(self._key))
        host_type, host_data, *_ = self._known_host.split()
        host_key = paramiko.Ed25519Key(data=base64.b64decode(host_data))

        class PinnedVendor:
            def run_command(
                inner_self: Any, host: str, command: str, username: str | None = None,
                port: int | None = None, **kwargs: Any,
            ) -> Any:
                if host != "ssh.github.com" or port != 443 or username != "git":
                    raise ValueError("QUEUE_SSH_DESTINATION_MISMATCH")
                client = paramiko.SSHClient()
                client.get_host_keys().add("[ssh.github.com]:443", host_type, host_key)
                client.set_missing_host_key_policy(paramiko.RejectPolicy())
                client.connect(
                    host, port=443, username="git", pkey=private,
                    allow_agent=False, look_for_keys=False,
                    timeout=6, auth_timeout=6, banner_timeout=6,
                )
                transport = client.get_transport()
                if transport is None:
                    raise ValueError("QUEUE_TRANSPORT_UNAVAILABLE")
                channel = transport.open_session(timeout=8)
                channel.settimeout(8)
                if kwargs.get("protocol_version") in (None, 2):
                    channel.set_environment_variable("GIT_PROTOCOL", "version=2")
                channel.exec_command(command)
                return _ParamikoWrapper(client, channel)

        return PinnedVendor()

    def _client(self) -> Any:
        from dulwich.client import SSHGitClient
        # porcelain.clone silently filters unknown transport kwargs in 0.24.10.
        # Bind the vendor on the client itself; never fall back to system ssh.
        return SSHGitClient("ssh.github.com", port=443, username="git", vendor=self._vendor())

    def checkout(self, destination: Path) -> Any:
        return self._client().clone(
            "/panxy1019/QTrade-research-queue.git", str(destination),
            checkout=True, depth=1, branch="main", progress=lambda _: None,
        )

    def append(self, section: str, identity: str, value: dict[str, Any]) -> None:
        if section not in {"requests", "cancellations"}:
            raise ValueError("APPEND_SECTION_FORBIDDEN")
        if re.fullmatch(r"[a-f0-9]{32}", identity) is None:
            raise ValueError("INVALID_JOB_ID")
        from dulwich import porcelain

        with tempfile.TemporaryDirectory(prefix="qtrade-queue-") as directory:
            destination = Path(directory) / "queue"
            repo = self.checkout(destination)
            try:
                target = destination / section / (identity + ".json")
                content = canonical(value)
                if target.exists():
                    if target.read_bytes() != content:
                        raise ValueError("IMMUTABLE_JOB_ID_CONFLICT")
                    return
                target.parent.mkdir(exist_ok=True)
                target.write_bytes(content)
                parent = repo.head()
                porcelain.add(repo, paths=[str(target)])
                porcelain.commit(repo, message=b"Append bounded research task", author=IDENTITY)

                def update_refs(refs: dict[bytes, bytes]) -> dict[bytes, bytes]:
                    if refs.get(b"refs/heads/main") != parent:
                        raise ValueError("QUEUE_CONCURRENT_UPDATE_RETRY")
                    return {b"refs/heads/main": repo.head()}

                def pack(have: Any, want: Any, **kwargs: Any) -> Any:
                    return repo.generate_pack_data(set(have), set(want), **kwargs)

                pushed = self._client().send_pack(
                    b"/panxy1019/QTrade-research-queue.git", update_refs,
                    generate_pack_data=pack, progress=lambda _: None,
                )
                if any((pushed.ref_status or {}).values()):
                    raise ValueError("QUEUE_PUSH_NOT_ACCEPTED")
            finally:
                repo.close()

    def read(self) -> dict[str, dict[str, Any]]:
        with tempfile.TemporaryDirectory(prefix="qtrade-queue-") as directory:
            destination = Path(directory) / "queue"
            repo = self.checkout(destination)
            try:
                result = {}
                for section in ("requests", "cancellations"):
                    for path in sorted((destination / section).glob("*.json")):
                        if re.fullmatch(r"[a-f0-9]{32}\.json", path.name) and (
                            path.stat().st_size <= 16000
                        ):
                            result[section + "/" + path.stem] = json.loads(path.read_bytes())
                if len(result) > 2000:
                    raise ValueError("QUEUE_RETENTION_LIMIT_REACHED")
                return result
            finally:
                repo.close()
