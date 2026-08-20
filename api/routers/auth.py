"""Authentication: register the first owner, log in, refresh, invite.

`/auth/register` only ever works once per deployment - it creates the tenant and
its owner. After that the only way in is an invitation, so a public URL cannot be
used to mint accounts on someone else's workspace.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status

from api.db import execute, fetch_one
from api.deps import (
    CurrentUserDep,
    SettingsDep,
    UnscopedDbDep,
    client_ip_inet,
    rate_limit,
    require_role,
)
from api.errors import ApiError, Conflict, Forbidden, NotFound, Unauthorized
from api.schemas import (
    AcceptInviteRequest,
    InviteRequest,
    InviteResponse,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from api.security import (
    create_access_token,
    create_refresh_token,
    generate_invitation_token,
    hash_password,
    hash_token,
    needs_rehash,
    refresh_expiry,
    verify_password,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

auth_rate_limit = Depends(rate_limit("auth", "rate_limit_auth_per_minute"))

INVITATION_TTL_DAYS = 7


def _record_auth_event(
    conn, *, email: str | None, event: str, request: Request,
    tenant_id: UUID | None = None, user_id: UUID | None = None,
) -> None:
    execute(
        conn,
        """
        INSERT INTO raw.auth_event (email, tenant_id, user_id, event, ip_address, user_agent)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            email,
            tenant_id,
            user_id,
            event,
            client_ip_inet(request),
            request.headers.get("user-agent", "")[:500],
        ),
    )


def _issue_tokens(conn, settings, user: dict) -> TokenResponse:
    access_token, expires_in = create_access_token(
        settings,
        user_id=user["id"],
        tenant_id=user["tenant_id"],
        email=user["email"],
        role=user["role"],
    )
    refresh_token, token_hash = create_refresh_token()
    execute(
        conn,
        "INSERT INTO core.refresh_token (user_id, token_hash, expires_at) VALUES (%s, %s, %s)",
        (user["id"], token_hash, refresh_expiry(settings)),
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
    )


@router.post(
    "/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[auth_rate_limit],
    summary="Crear el primer propietario del despliegue",
)
def register(
    payload: RegisterRequest,
    request: Request,
    conn: UnscopedDbDep,
    settings: SettingsDep,
) -> TokenResponse:
    existing = fetch_one(conn, "SELECT count(*) AS n FROM core.app_user")
    if existing and existing["n"] > 0:
        raise Forbidden(
            "Este despliegue ya tiene usuarios. Pídele a la persona propietaria "
            "que te envíe una invitación."
        )

    slug = payload.tenant_name.lower().replace(" ", "-")[:40] or "dataeffi"
    tenant = fetch_one(
        conn,
        "INSERT INTO core.tenant (slug, name) VALUES (%s, %s) RETURNING id, name",
        (slug, payload.tenant_name),
    )

    try:
        password_hash = hash_password(payload.password)
    except ValueError as exc:
        raise ApiError("weak_password", str(exc)) from exc

    user = fetch_one(
        conn,
        """
        INSERT INTO core.app_user (tenant_id, email, password_hash, full_name, role)
        VALUES (%s, %s, %s, %s, 'owner')
        RETURNING id, tenant_id, email, role
        """,
        (tenant["id"], payload.email.lower(), password_hash, payload.full_name),
    )

    _record_auth_event(
        conn, email=payload.email, event="register", request=request,
        tenant_id=tenant["id"], user_id=user["id"],
    )
    logger.info("first owner registered for tenant %s", tenant["id"])
    return _issue_tokens(conn, settings, user)


@router.post(
    "/login",
    response_model=TokenResponse,
    dependencies=[auth_rate_limit],
    summary="Iniciar sesión",
)
def login(
    payload: LoginRequest,
    request: Request,
    conn: UnscopedDbDep,
    settings: SettingsDep,
) -> TokenResponse:
    user = fetch_one(
        conn,
        """
        SELECT id, tenant_id, email, role, password_hash, is_active
        FROM core.app_user WHERE lower(email) = lower(%s)
        """,
        (payload.email,),
    )

    # Same message and roughly the same work whether the user exists or not:
    # a faster "no such user" response is an account enumeration oracle.
    if user is None or not verify_password(user["password_hash"], payload.password):
        _record_auth_event(conn, email=payload.email, event="login_failed", request=request)
        raise Unauthorized("Correo o contraseña incorrectos")

    if not user["is_active"]:
        raise Forbidden("Esta cuenta está desactivada")

    if needs_rehash(user["password_hash"]):
        execute(
            conn,
            "UPDATE core.app_user SET password_hash = %s WHERE id = %s",
            (hash_password(payload.password), user["id"]),
        )

    execute(conn, "UPDATE core.app_user SET last_login_at = now() WHERE id = %s", (user["id"],))
    _record_auth_event(
        conn, email=payload.email, event="login_ok", request=request,
        tenant_id=user["tenant_id"], user_id=user["id"],
    )
    return _issue_tokens(conn, settings, user)


@router.post("/refresh", response_model=TokenResponse, summary="Renovar el token de acceso")
def refresh(
    payload: RefreshRequest,
    request: Request,
    conn: UnscopedDbDep,
    settings: SettingsDep,
) -> TokenResponse:
    token_hash = hash_token(payload.refresh_token)
    row = fetch_one(
        conn,
        """
        SELECT rt.id AS token_id, u.id, u.tenant_id, u.email, u.role, u.is_active,
               rt.expires_at, rt.revoked_at
        FROM core.refresh_token rt
        JOIN core.app_user u ON u.id = rt.user_id
        WHERE rt.token_hash = %s
        """,
        (token_hash,),
    )

    if row is None or row["revoked_at"] is not None:
        raise Unauthorized("Sesión inválida. Vuelve a iniciar sesión.")
    if row["expires_at"] < datetime.now(UTC):
        raise Unauthorized("Sesión expirada. Vuelve a iniciar sesión.")
    if not row["is_active"]:
        raise Forbidden("Esta cuenta está desactivada")

    # Rotation: the presented token dies the moment it is used.
    execute(
        conn, "UPDATE core.refresh_token SET revoked_at = now() WHERE id = %s", (row["token_id"],)
    )
    _record_auth_event(
        conn, email=row["email"], event="refresh", request=request,
        tenant_id=row["tenant_id"], user_id=row["id"],
    )
    return _issue_tokens(conn, settings, row)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    summary="Cerrar sesión",
)
def logout(payload: RefreshRequest, conn: UnscopedDbDep) -> None:
    execute(
        conn,
        "UPDATE core.refresh_token SET revoked_at = now() "
        "WHERE token_hash = %s AND revoked_at IS NULL",
        (hash_token(payload.refresh_token),),
    )


@router.get("/me", response_model=UserResponse, summary="Quién soy")
def me(user: CurrentUserDep, conn: UnscopedDbDep) -> UserResponse:
    row = fetch_one(
        conn,
        """
        SELECT u.id, u.email, u.full_name, u.role, u.tenant_id, u.created_at, t.name AS tenant_name
        FROM core.app_user u
        JOIN core.tenant t ON t.id = u.tenant_id
        WHERE u.id = %s
        """,
        (user.id,),
    )
    if row is None:
        raise NotFound("El usuario del token ya no existe")
    return UserResponse(**row)


@router.post(
    "/invite",
    response_model=InviteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invitar a alguien al workspace",
)
def invite(
    payload: InviteRequest,
    conn: UnscopedDbDep,
    user: Annotated[object, Depends(require_role("owner"))],
) -> InviteResponse:
    existing = fetch_one(
        conn,
        "SELECT 1 FROM core.app_user WHERE lower(email) = lower(%s) AND tenant_id = %s",
        (payload.email, user.tenant_id),
    )
    if existing:
        raise Conflict("Esa persona ya tiene cuenta en este workspace")

    token, token_hash = generate_invitation_token()
    expires_at = datetime.now(UTC) + timedelta(days=INVITATION_TTL_DAYS)
    row = fetch_one(
        conn,
        """
        INSERT INTO core.invitation (tenant_id, email, role, token_hash, invited_by, expires_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id, email, role, expires_at
        """,
        (user.tenant_id, payload.email.lower(), payload.role, token_hash, user.id, expires_at),
    )
    return InviteResponse(**row, invitation_token=token)


@router.post(
    "/accept-invite",
    response_model=TokenResponse,
    dependencies=[auth_rate_limit],
    summary="Aceptar una invitación y crear la cuenta",
)
def accept_invite(
    payload: AcceptInviteRequest,
    request: Request,
    conn: UnscopedDbDep,
    settings: SettingsDep,
) -> TokenResponse:
    invitation = fetch_one(
        conn,
        """
        SELECT id, tenant_id, email, role, expires_at, accepted_at
        FROM core.invitation WHERE token_hash = %s
        """,
        (hash_token(payload.token),),
    )
    if invitation is None:
        raise NotFound("Esa invitación no existe")
    if invitation["accepted_at"] is not None:
        raise Conflict("Esa invitación ya fue usada")
    if invitation["expires_at"] < datetime.now(UTC):
        raise ApiError("invitation_expired", "La invitación expiró. Pide una nueva.")

    try:
        password_hash = hash_password(payload.password)
    except ValueError as exc:
        raise ApiError("weak_password", str(exc)) from exc

    user = fetch_one(
        conn,
        """
        INSERT INTO core.app_user (tenant_id, email, password_hash, full_name, role)
        VALUES (%s, %s, %s, %s, %s)
        RETURNING id, tenant_id, email, role
        """,
        (
            invitation["tenant_id"],
            invitation["email"],
            password_hash,
            payload.full_name,
            invitation["role"],
        ),
    )
    execute(
        conn, "UPDATE core.invitation SET accepted_at = now() WHERE id = %s", (invitation["id"],)
    )
    _record_auth_event(
        conn, email=invitation["email"], event="register", request=request,
        tenant_id=invitation["tenant_id"], user_id=user["id"],
    )
    return _issue_tokens(conn, settings, user)
