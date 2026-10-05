import secrets

import jwt
from fastapi import Header, Request

from packages.domain.errors import DomainError


def authenticated_owner(request: Request, authorization: str | None = Header(default=None)) -> str:
    settings = request.app.state.settings
    if not authorization or not authorization.startswith("Bearer "):
        raise DomainError("UNAUTHENTICATED", "请提供 Bearer 访问凭据", 401)
    token = authorization[7:]
    if settings.auth_mode == "development":
        if not secrets.compare_digest(token, settings.dev_auth_token):
            raise DomainError("UNAUTHENTICATED", "访问凭据无效", 401)
        return settings.dev_owner_id
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"],
                             issuer=settings.jwt_issuer, audience=settings.jwt_audience,
                             options={"require": ["sub", "exp", "iss", "aud"]})
        subject = payload["sub"]
        if not isinstance(subject, str) or not subject or len(subject) > 200:
            raise ValueError("Invalid subject")
        return subject
    except (jwt.InvalidTokenError, ValueError):
        raise DomainError("UNAUTHENTICATED", "访问凭据无效或已过期", 401) from None
