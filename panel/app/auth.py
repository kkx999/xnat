import re
import secrets
from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error, VerifyMismatchError
from fastapi import HTTPException, Request
from sqlalchemy.orm import Session
from .models import User
from .security import get_login_session

password_hasher = PasswordHasher()
USERNAME_RE = re.compile(r"^[a-zA-Z0-9_]{3,32}$")


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    try:
        return password_hasher.verify(hashed, password)
    except (VerifyMismatchError, Argon2Error):
        return False


def validate_username(username: str) -> bool:
    return bool(USERNAME_RE.fullmatch(username))


def current_user(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    user = db.get(User, int(user_id))
    if not user or not user.is_active:
        request.session.clear()
        return None

    # Every authenticated browser session must have a matching server-side
    # login session so it can be revoked immediately from the account panel.
    if not request.session.get("login_session_token") or not get_login_session(db, request):
        request.session.clear()
        return None
    return user


def login_required(request: Request, db: Session) -> User:
    user = current_user(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


def admin_required(request: Request, db: Session) -> User:
    user = login_required(request, db)
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def ensure_csrf(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def validate_csrf(request: Request, token: str):
    expected = request.session.get("csrf_token")
    if not expected or not secrets.compare_digest(expected, token or ""):
        raise HTTPException(status_code=403, detail="CSRF 校验失败")
