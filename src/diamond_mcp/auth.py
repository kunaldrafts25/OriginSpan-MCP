"""OIDC resource server; identity and project grants come from the customer IdP."""
from __future__ import annotations

from .compat import required, getenv
import asyncio
import os
from urllib.parse import urlparse

import jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier

from .engine import Actor
from .compat import claim


class OIDCVerifier(TokenVerifier):
    def __init__(self, issuer=None, audience=None, jwks=None):
        self.issuer = issuer or required("DIAMOND_OIDC_ISSUER")
        self.audience = audience or required("DIAMOND_RESOURCE_URL")
        self.jwks_url = jwks or required("DIAMOND_OIDC_JWKS")
        for value in (self.issuer, self.audience, self.jwks_url):
            url = urlparse(value)
            if url.scheme != "https" and not (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1"}):
                raise ValueError("OIDC endpoints require HTTPS (loopback HTTP is for integration tests)")
        self.keys = jwt.PyJWKClient(self.jwks_url, cache_keys=True, lifespan=300, timeout=5)

    def decode(self, token):
        key = self.keys.get_signing_key_from_jwt(token).key
        claims = jwt.decode(token, key, algorithms=["RS256"], audience=self.audience,
                            issuer=self.issuer, options={"require": ["exp", "iat", "sub", "iss", "aud"]})
        projects = claim(claims, "projects")
        if not isinstance(projects, list) or not all(isinstance(p, str) for p in projects):
            raise jwt.InvalidTokenError("Missing project grants")
        return claims

    async def verify_token(self, token):
        try:
            claims = await asyncio.to_thread(self.decode, token)
            scopes = ["originspan:" + s[8:] if s.startswith("diamond:") else s
                      for s in str(claims.get("scope", "")).split()]
            return AccessToken(token=token, client_id=str(claims.get("azp", claims["sub"])), subject=claims["sub"],
                scopes=scopes, expires_at=claims["exp"], resource=self.audience, claims=claims)
        except (jwt.PyJWTError, OSError, ValueError):
            return None


def validate_demo_settings(token, project):
    import re
    if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,256}", token):
        raise ValueError("ORIGINSPAN_DEMO_TOKEN must be a generated 32-256 character URL-safe secret")
    Actor("demo-user", frozenset({project})).require(project, "read")


class DemoTokenVerifier(TokenVerifier):
    """Explicit single-project demo credential, never an OAuth/enterprise IdP."""
    is_static = True

    def __init__(self, token=None, audience=None, project=None):
        self._secret = token if token is not None else required("ORIGINSPAN_DEMO_TOKEN")
        self.project = project if project is not None else getenv("ORIGINSPAN_PROJECT", "demo")
        validate_demo_settings(self._secret, self.project)
        self.audience = audience or required("ORIGINSPAN_RESOURCE_URL")
        parsed = urlparse(self.audience)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost"}):
            raise ValueError("Demo endpoint requires HTTPS except for loopback testing")
        if not parsed.netloc or parsed.path != "/mcp" or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Demo resource must be an absolute /mcp URL without credentials")
        self.issuer = self.audience

    async def verify_token(self, token):
        import hmac
        if not hmac.compare_digest(token.encode("utf-8"), self._secret.encode("ascii")):
            return None
        scopes = ["originspan:read", "originspan:analyze", "originspan:propose"]
        return AccessToken(token=token, client_id="originspan-demo", subject="originspan-demo-user",
            scopes=scopes, resource=self.audience, claims={"sub":"originspan-demo-user",
                "originspan_projects":[self.project], "scope":" ".join(scopes)})


def configured_verifier():
    mode = getenv("ORIGINSPAN_AUTH_MODE", "oidc")
    if mode == "demo-token":
        return DemoTokenVerifier()
    if mode == "oidc":
        return OIDCVerifier()
    raise ValueError("ORIGINSPAN_AUTH_MODE must be oidc or demo-token")


def actor_from_claims(claims):
    scopes = frozenset(s.split(":", 1)[1] for s in str(claims.get("scope", "")).split()
                       if s.startswith(("originspan:", "diamond:")))
    apps = claim(claims, "applications")
    if apps is not None and (not isinstance(apps, list) or not all(isinstance(a, str) for a in apps)):
        raise PermissionError("Invalid application grants")
    return Actor(claims["sub"], frozenset(claim(claims, "projects") or []), scopes,
                 frozenset(apps) if apps is not None else None)
