"""Request authentication. The caller's role always comes from here, never from the request body.

Modes (``AUTH_MODE``):
  api_key  ``X-API-Key: <key>``. Keys live in a JSON file (``API_KEYS_FILE``) that stores only
           SHA-256 hashes: {"keys": [{"id": "...", "hash": "<sha256 hex>", "principal": "alice",
           "role": "employee"}]}. Create entries with ``scripts/create_api_key.py``.
  jwt      ``Authorization: Bearer <token>``. HS256 (``JWT_SECRET``, >= 32 bytes) or RS256
           (``JWT_PUBLIC_KEY_FILE``). The algorithm is pinned, ``exp`` and ``sub`` are required,
           ``aud``/``iss`` are checked when configured, and the role is read from ``JWT_ROLE_CLAIM``.
  dev      ``X-Dev-Role: <role>`` picks any role, for the local demo UI only. Refused unless
           ``ENV=dev``, and it logs a warning at startup.
"""
from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from securerag.security.rbac import ROLE_POLICY

logger = logging.getLogger(__name__)

DEV_ROLE_HEADER = "x-dev-role"
API_KEY_HEADER = "x-api-key"


class AuthError(Exception):
    """Authentication failed (HTTP 401). The message is safe to return to the client."""


class AuthConfigError(RuntimeError):
    """Authentication is misconfigured; the service must not start."""


@dataclass(frozen=True)
class Principal:
    id: str
    role: str
    method: str


def hash_api_key(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class Authenticator:
    def __init__(self, settings: Any):
        self.mode = settings.auth_mode
        self.settings = settings
        if self.mode == "dev":
            if settings.env != "dev":
                raise AuthConfigError("AUTH_MODE=dev is only allowed with ENV=dev")
            logger.warning("*" * 72)
            logger.warning("AUTH_MODE=dev: any caller can pick any role with the X-Dev-Role header. "
                           "Never expose this service outside a development machine.")
            logger.warning("*" * 72)
        elif self.mode == "api_key":
            self._keys = self._load_keys(Path(settings.api_keys_file))
        elif self.mode == "jwt":
            self._jwt_key, self._jwt_alg = self._load_jwt_key(settings)
        else:
            raise AuthConfigError(f"unknown AUTH_MODE {self.mode!r}")

    # ------------------------------------------------------------------ config
    @staticmethod
    def _load_keys(path: Path) -> dict[str, Principal]:
        if not path.exists():
            raise AuthConfigError(f"API keys file {path} not found; create one with scripts/create_api_key.py")
        data = json.loads(path.read_text(encoding="utf-8"))
        keys: dict[str, Principal] = {}
        for entry in data.get("keys", []):
            role = entry.get("role")
            if role not in ROLE_POLICY:
                raise AuthConfigError(f"API key {entry.get('id')!r} has unknown role {role!r}")
            if entry.get("disabled"):
                continue
            keys[entry["hash"].lower()] = Principal(id=entry.get("principal") or entry["id"], role=role,
                                                     method="api_key")
        if not keys:
            raise AuthConfigError(f"API keys file {path} contains no enabled keys")
        return keys

    @staticmethod
    def _load_jwt_key(settings: Any) -> tuple[Any, str]:
        alg = settings.jwt_algorithm
        if alg == "HS256":
            secret = settings.jwt_secret
            if len(secret.encode("utf-8")) < 32:
                raise AuthConfigError("JWT_SECRET must be at least 32 bytes for HS256")
            return secret, alg
        if alg == "RS256":
            path = Path(settings.jwt_public_key_file or "")
            if not path.is_file():
                raise AuthConfigError("JWT_PUBLIC_KEY_FILE must point to a PEM public key for RS256")
            return path.read_text(encoding="utf-8"), alg
        raise AuthConfigError(f"unsupported JWT_ALGORITHM {alg!r}; use HS256 or RS256")

    # ------------------------------------------------------------------ requests
    def authenticate(self, headers: Mapping[str, str]) -> Principal:
        h = {k.lower(): v for k, v in headers.items()}
        if self.mode == "dev":
            role = h.get(DEV_ROLE_HEADER, "guest").strip()
            if role not in ROLE_POLICY:
                raise AuthError("unknown role in X-Dev-Role")
            return Principal(id=f"dev:{role}", role=role, method="dev")
        if self.mode == "api_key":
            key = h.get(API_KEY_HEADER, "").strip()
            if not key:
                raise AuthError("missing X-API-Key header")
            principal = self._keys.get(hash_api_key(key))
            if principal is None:
                raise AuthError("invalid API key")
            return principal
        return self._authenticate_jwt(h)

    def _authenticate_jwt(self, h: Mapping[str, str]) -> Principal:
        import jwt

        auth = h.get("authorization", "")
        if not auth.lower().startswith("bearer "):
            raise AuthError("missing bearer token")
        token = auth[7:].strip()
        options: dict[str, Any] = {"require": ["exp", "sub"]}
        kwargs: dict[str, Any] = {}
        if self.settings.jwt_audience:
            kwargs["audience"] = self.settings.jwt_audience
            options["require"].append("aud")
        if self.settings.jwt_issuer:
            kwargs["issuer"] = self.settings.jwt_issuer
            options["require"].append("iss")
        try:
            claims = jwt.decode(token, self._jwt_key, algorithms=[self._jwt_alg], options=cast(Any, options),
                                leeway=self.settings.jwt_leeway_seconds, **kwargs)
        except jwt.ExpiredSignatureError as exc:
            raise AuthError("token expired") from exc
        except jwt.PyJWTError as exc:
            raise AuthError("invalid token") from exc
        role = claims.get(self.settings.jwt_role_claim)
        if role not in ROLE_POLICY:
            raise AuthError("token has no valid role claim")
        return Principal(id=str(claims["sub"]), role=role, method="jwt")
