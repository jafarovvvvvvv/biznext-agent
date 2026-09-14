from __future__ import annotations

import hmac
from typing import Literal

from fastapi import Header, Query, Request

from app.container import Container
from app.services.ask import ApiError

Lang = Literal["ru", "en"]


def get_container(request: Request) -> Container:
    return request.app.state.container


def lang_param(lang: str = Query("ru")) -> str:
    return "en" if lang == "en" else "ru"


def require_admin(request: Request, x_admin_token: str | None = Header(default=None)) -> None:
    container: Container = request.app.state.container
    expected = container.settings.admin_token_value
    if expected and not (x_admin_token and hmac.compare_digest(x_admin_token, expected)):
        raise ApiError(401, "unauthorized", "admin token required")
