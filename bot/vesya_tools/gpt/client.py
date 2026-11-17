"""mTLS-only client for Novosibirsk's independent GPT account API.

Not used by the running Vesya bot until explicitly wired and deployed.
All operations require a HTTPS endpoint, a client certificate, a client
private key, and a separate application token. Server certificate verification
uses system trust (Let's Encrypt); a CA bundle override is optional.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import ssl
from urllib.parse import urlsplit

import httpx


ALLOWED_ACTIONS = frozenset({
    "create_user", "get_user_status", "disable_user", "enable_user",
    "rotate_password", "get_service_status",
})


class GPTAgentError(Exception):
    """Client error without sensitive response content or credentials."""


@dataclass(frozen=True)
class GPTAgentConfig:
    base_url: str
    token: str = field(repr=False)
    ca_file: str
    client_cert_file: str
    client_key_file: str = field(repr=False)

    def __post_init__(self):
        url = urlsplit(self.base_url)
        if (
            url.scheme != "https" or not url.hostname or not url.port
            or url.username or url.password or url.query or url.fragment
            or url.path not in ("", "/")
        ):
            raise ValueError("GPT agent requires a dedicated HTTPS host:port endpoint")
        if not self.token or len(self.token) < 32:
            raise ValueError("GPT agent token is missing or too short")
        if not all((self.client_cert_file, self.client_key_file)):
            raise ValueError("GPT agent mTLS certificate paths are required")

    @classmethod
    def from_env(cls) -> GPTAgentConfig:
        return cls(
            base_url=os.getenv("GPT_AGENT_API_URL", "").strip(),
            token=os.getenv("GPT_AGENT_API_TOKEN", "").strip(),
            ca_file=os.getenv("GPT_AGENT_CA_FILE", "").strip(),
            client_cert_file=os.getenv("GPT_AGENT_CLIENT_CERT_FILE", "").strip(),
            client_key_file=os.getenv("GPT_AGENT_CLIENT_KEY_FILE", "").strip(),
        )


class GPTAgentClient:
    def __init__(self, config: GPTAgentConfig):
        self.config = config

    def _tls_context(self) -> ssl.SSLContext:
        for path in (
            self.config.client_cert_file, self.config.client_key_file,
        ):
            if not Path(path).is_file():
                raise GPTAgentError("GPT agent mTLS credentials are unavailable")
        # The server uses a public Let's Encrypt certificate. The private\n        # Vesya client CA is trusted by Nginx, not by the client for the server.\n        context = ssl.create_default_context(cafile=self.config.ca_file or None)
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_cert_chain(
            certfile=self.config.client_cert_file,
            keyfile=self.config.client_key_file,
        )
        return context

    async def call(self, action: str, *, telegram_id: int | None = None) -> dict:
        if action not in ALLOWED_ACTIONS:
            raise ValueError("Unsupported GPT agent operation")
        if action != "get_service_status" and (
            isinstance(telegram_id, bool) or not isinstance(telegram_id, int)
            or not 0 < telegram_id < 2**63
        ):
            raise ValueError("telegram_id must be a positive integer")
        payload = {} if action == "get_service_status" else {"telegram_id": telegram_id}
        url = self.config.base_url.rstrip("/") + "/v1/" + action
        try:
            async with httpx.AsyncClient(
                verify=self._tls_context(),
                timeout=httpx.Timeout(12.0, connect=5.0),
                follow_redirects=False,
                trust_env=False,
            ) as client:
                response = await client.post(
                    url,
                    headers={"X-GPT-Agent-Token": self.config.token},
                    json=payload,
                )
        except (httpx.HTTPError, ssl.SSLError, OSError):
            # Never echo HTTP bodies, tokens, credentials, or internal URLs.
            raise GPTAgentError("GPT agent connection failed") from None
        if response.status_code != 200:
            raise GPTAgentError(f"GPT agent rejected request (HTTP {response.status_code})")
        try:
            result = response.json()
        except ValueError:
            raise GPTAgentError("GPT agent returned invalid JSON") from None
        if not isinstance(result, dict) or "status" not in result:
            raise GPTAgentError("GPT agent returned malformed response")
        return result

    async def create_user(self, telegram_id: int) -> dict:
        return await self.call("create_user", telegram_id=telegram_id)

    async def get_user_status(self, telegram_id: int) -> dict:
        return await self.call("get_user_status", telegram_id=telegram_id)

    async def disable_user(self, telegram_id: int) -> dict:
        return await self.call("disable_user", telegram_id=telegram_id)

    async def enable_user(self, telegram_id: int) -> dict:
        return await self.call("enable_user", telegram_id=telegram_id)

    async def rotate_password(self, telegram_id: int) -> dict:
        return await self.call("rotate_password", telegram_id=telegram_id)

    async def get_service_status(self) -> dict:
        return await self.call("get_service_status")
