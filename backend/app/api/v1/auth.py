import logging
import os
import random
import time
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends
from jose import jwt
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_service_auth
from app.core.config import Settings, get_settings
from app.core.exceptions import AppError
from app.db.models.user import User
from app.db.session import get_db
from app.schemas.common import AuthLoginRequest, AuthLogoutResponse, AuthTokenResponse
from app.services.user_ids import normalize_user_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# ─── 验证码存储 ───
# {phone: {"code": "123456", "expires": timestamp, "attempts": [timestamps]}}
_dev_codes: dict[str, dict] = {}
CODE_EXPIRE_SECONDS = 300  # 5 分钟过期
RATE_LIMIT_WINDOW = 60     # 频率限制窗口（秒）
RATE_LIMIT_MAX = 5         # 每分钟最多发送次数


def _check_rate_limit(phone: str) -> None:
    """检查频率限制：同一手机号每分钟最多发送 5 次验证码"""
    now = time.time()
    if phone not in _dev_codes:
        _dev_codes[phone] = {"attempts": []}
    attempts = _dev_codes[phone]["attempts"]
    # 清理过期记录
    attempts[:] = [t for t in attempts if now - t < RATE_LIMIT_WINDOW]
    if len(attempts) >= RATE_LIMIT_MAX:
        raise AppError(
            "rate_limited",
            f"发送过于频繁，请 {RATE_LIMIT_WINDOW} 秒后再试",
            status_code=429,
        )
    attempts.append(now)


def _create_access_token(settings: Settings, user_id: str) -> str:
    if not settings.jwt_secret:
        raise AppError(
            "jwt_not_configured",
            "未配置 JWT_SECRET",
            status_code=503,
        )
    now = datetime.utcnow()
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=7)).timestamp()),
        "type": "access",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def _create_refresh_token(settings: Settings, user_id: str) -> str:
    if not settings.jwt_secret:
        raise AppError(
            "jwt_not_configured",
            "未配置 JWT_SECRET",
            status_code=503,
        )
    now = datetime.utcnow()
    payload = {
        "sub": user_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(days=30)).timestamp()),
        "type": "refresh",
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


class SendCodeRequest(BaseModel):
    phone: str


@router.post("/send-code")
async def send_code(
    body: SendCodeRequest,
    settings: Annotated[Settings, Depends(get_settings)],
):
    """发送验证码 - 开发模式：随机生成验证码"""
    if not body.phone or len(body.phone) != 11:
        raise AppError("invalid_phone", "手机号格式错误", status_code=400)

    # 频率限制检查
    _check_rate_limit(body.phone)

    # 生成 6 位随机验证码
    code = str(random.randint(100000, 999999))

    # 存储验证码（带过期时间）
    _dev_codes[body.phone] = {
        "code": code,
        "expires": time.time() + CODE_EXPIRE_SECONDS,
        "attempts": _dev_codes.get(body.phone, {}).get("attempts", []),
    }

    # 仅在开发环境返回验证码
    resp = {
        "message": "验证码已发送",
        "phone": body.phone,
    }
    if os.getenv("ENV", "production") == "development":
        resp["dev_code"] = code

    return resp


@router.post("/login", response_model=AuthTokenResponse)
async def login(
    body: AuthLoginRequest,
    settings: Annotated[Settings, Depends(get_settings)],
    session: Annotated[AsyncSession, Depends(get_db)],
) -> AuthTokenResponse:
    if body.phone:
        if not body.code:
            raise AppError(
                "missing_code",
                "手机号登录需要提供验证码",
                status_code=400,
            )

        # 验证存储的验证码
        stored = _dev_codes.get(body.phone)
        if not stored:
            raise AppError("invalid_code", "验证码不存在或已过期", status_code=401)

        # 检查是否过期
        if time.time() > stored["expires"]:
            _dev_codes.pop(body.phone, None)
            raise AppError("code_expired", "验证码已过期，请重新获取", status_code=401)

        # 验证验证码
        if stored["code"] != body.code:
            raise AppError("invalid_code", "验证码错误", status_code=401)

        # 验证成功后清除验证码（防止重放）
        _dev_codes.pop(body.phone, None)

        # 查找或创建用户
        result = await session.execute(select(User).where(User.phone == body.phone))
        user = result.scalar_one_or_none()
        if user is None:
            uid = normalize_user_id(body.phone, settings)
            user = User(
                id=uid,
                nickname=body.nickname or "用户",
                user_type="personal",
                phone=body.phone,
            )
            session.add(user)
            await session.flush()
        elif body.nickname and user.nickname == "用户":
            user.nickname = body.nickname
            await session.flush()
    else:
        uid = normalize_user_id(body.user_id, settings)
        result = await session.execute(select(User).where(User.id == uid))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                id=uid,
                nickname=body.nickname or "用户",
                user_type="personal",
                phone=None,
            )
            session.add(user)
            await session.flush()
        elif body.nickname and user.nickname == "用户":
            user.nickname = body.nickname
            await session.flush()

    token = _create_access_token(settings, user.id)
    refresh_token = _create_refresh_token(settings, user.id)
    return AuthTokenResponse(
        access_token=token,
        token_type="bearer",
        user_id=user.id,
        refresh_token=refresh_token,
    )


@router.post("/logout", dependencies=[Depends(require_service_auth)], response_model=AuthLogoutResponse)
async def logout() -> AuthLogoutResponse:
    return AuthLogoutResponse(message="已登出")


@router.post("/refresh")
async def refresh_token(
    body: dict,
    settings: Annotated[Settings, Depends(get_settings)],
) -> dict:
    """使用 refresh token 获取新的 access token"""
    refresh_token_str = body.get("refresh_token")
    if not refresh_token_str:
        raise AppError("missing_refresh_token", "缺少 refresh_token", status_code=400)

    try:
        payload = jwt.decode(
            refresh_token_str,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
    except Exception:
        raise AppError("invalid_refresh_token", "refresh_token 无效或已过期", status_code=401)

    if payload.get("type") != "refresh":
        raise AppError("invalid_token_type", "不是有效的 refresh_token", status_code=401)

    user_id = payload.get("sub")
    if not user_id:
        raise AppError("invalid_refresh_token", "refresh_token 中缺少用户信息", status_code=401)

    new_access_token = _create_access_token(settings, user_id)
    return {
        "access_token": new_access_token,
        "token_type": "bearer",
    }
