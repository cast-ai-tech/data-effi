"""Authentication: register, log in, refresh, invite.

`/auth/register` creates an organisation, its first company and its owner, on
a free month (migration 048). It is open: every registration is its own
organisation, isolated by tenant like any other. Joining an EXISTING company
is still only possible by invitation.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status

from api.billing import start_trial, subscription_state
from api.db import execute, fetch_all, fetch_one, fetch_required
from api.deps import (
    CAPABILITIES,
    CurrentUser,
    CurrentUserDep,
    SettingsDep,
    UnscopedDbDep,
    client_ip_inet,
    rate_limit,
    require_cap,
)
from api.errors import ApiError, Conflict, Forbidden, NotFound, Unauthorized
from api.schemas import (
    AcceptInviteRequest,
    Capability,
    InviteRequest,
    InviteResponse,
    LoginRequest,
    OrgRole,
    PasswordChangeRequest,
    ProfileUpdateRequest,
    RefreshRequest,
    RegisterRequest,
    Role,
    SessionRow,
    SubscriptionSummary,
    SwitchWorkspaceRequest,
    TokenResponse,
    UserResponse,
    WorkspaceSummary,
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


def _require_tenant(user: CurrentUser) -> UUID:
    """The company the caller is standing in, or a clear refusal."""
    if user.tenant_id is None:
        raise Forbidden(
            "Tu usuario no está en ninguna empresa. Elige una y vuelve a intentar."
        )
    return user.tenant_id


def _assert_countries_active(conn, tenant_id: UUID, countries: list[str]) -> None:
    """A scope may only name countries the company actually operates in.

    Otherwise a typo ('CO' for Colombia written 'CL') silently produces a
    membership that can see nothing, and the partner reports a broken dashboard
    instead of a wrong letter.
    """
    # This endpoint runs on an unscoped connection (it reads core.app_user,
    # which login needs before any tenant exists), and core.workspace_country is
    # under row-level security - unscoped it returns zero rows and every country
    # would look invalid. Scope the transaction just for this read.
    execute(conn, "SELECT set_config('norte.tenant_id', %s, true)", (str(tenant_id),))
    rows = fetch_all(
        conn,
        "SELECT country_code FROM core.workspace_country "
        "WHERE tenant_id = %s AND is_active",
        (tenant_id,),
    )
    active = {row["country_code"].upper() for row in rows}
    unknown = [c for c in countries if c.upper() not in active]
    if unknown:
        raise ApiError(
            "unknown_country",
            f"Esta sociedad no opera en: {', '.join(unknown)}. "
            f"Países activos: {', '.join(sorted(active)) or 'ninguno'}.",
        )


def _unique_slug(conn, table: str, base: str) -> str:
    """`base`, or `base-2`, `base-3`... - the first one nobody holds.

    `table` is one of two literals in this file, never request input.
    """
    assert table in {"core.org", "core.tenant"}
    candidate, n = base, 1
    while fetch_one(conn, f"SELECT 1 FROM {table} WHERE slug = %s", (candidate,)):  # noqa: S608
        n += 1
        candidate = f"{base}-{n}"
    return candidate


def slugify(name: str) -> str:
    """A URL-safe handle for a company or an org. Never the identity - the uuid is."""
    cleaned = "".join(c if c.isalnum() else "-" for c in name.lower().strip())
    return "-".join(part for part in cleaned.split("-") if part)[:40]

auth_rate_limit = Depends(rate_limit("auth", "rate_limit_auth_per_minute"))

INVITATION_TTL_DAYS = 7

# Verified against on login attempts for emails that do not exist, so the
# request costs the same argon2 work as a real one. Computed once at import;
# the value is never compared for equality with anything a user sends.
_DUMMY_HASH = hash_password("not-a-real-password-just-timing-ballast")


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


def _workspaces(conn, user_id: UUID) -> list[dict]:
    """Every company this person may open, with the grant attached."""
    return fetch_all(conn, "SELECT * FROM core.user_workspaces(%s)", (user_id,))


def _pick_workspace(
    workspaces: list[dict], wanted: UUID | None, fallback: UUID | None
) -> dict | None:
    """Which company this token stands in.

    Order: the one explicitly asked for, then the person's default, then the
    first one they have. An org admin with no membership gets None and can still
    read the consolidated roll-up.
    """
    for target in (wanted, fallback):
        if target is not None:
            for ws in workspaces:
                if ws["tenant_id"] == target:
                    return ws
    return workspaces[0] if workspaces else None


def _org_role(conn, user_id: UUID) -> OrgRole | None:
    """What this person may do ACROSS the companies of their org, if anything.

    Read at every mint, exactly like the memberships beside it: a role revoked
    an hour ago must not survive in the next token just because the session did.
    """
    row = fetch_one(conn, "SELECT role FROM core.user_org_role(%s)", (user_id,))
    # The column is constrained to the same three values as the Literal.
    return cast(OrgRole, row["role"]) if row else None


def _issue_tokens(conn, settings, user: dict, *, tenant_id: UUID | None = None) -> TokenResponse:
    """Mint a session standing in exactly one company.

    The role in the token is the role of the MEMBERSHIP, not the legacy column
    on the user row: the same person is owner of their own company and viewer of
    a partner's, and only the membership knows which one applies here.
    """
    workspaces = _workspaces(conn, user["id"])
    active = _pick_workspace(workspaces, tenant_id, user.get("tenant_id"))
    org_role = _org_role(conn, user["id"])

    if active is not None:
        active_tenant = active["tenant_id"]
        role = active["role"]
        countries = list(active["country_scope"]) if active["country_scope"] else None
    else:
        # No membership anywhere: only an org admin can do anything from here.
        active_tenant = None
        role = user.get("role") or "viewer"
        countries = None

    access_token, expires_in = create_access_token(
        settings,
        user_id=user["id"],
        tenant_id=active_tenant,
        email=user["email"],
        role=role,
        org_id=user.get("org_id"),
        is_org_admin=bool(user.get("is_org_admin")),
        org_role=org_role,
        countries=countries,
    )
    refresh_token, token_hash = create_refresh_token()
    execute(
        conn,
        "INSERT INTO core.refresh_token (user_id, token_hash, expires_at, tenant_id) "
        "VALUES (%s, %s, %s, %s)",
        (user["id"], token_hash, refresh_expiry(settings), active_tenant),
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=expires_in,
        tenant_id=active_tenant,
        tenant_name=active["tenant_name"] if active else None,
        role=role,
        is_org_admin=bool(user.get("is_org_admin")) or org_role == "admin",
        org_role=org_role,
        countries=countries,
        workspaces=[
            WorkspaceSummary(
                tenant_id=ws["tenant_id"],
                name=ws["tenant_name"],
                slug=ws["tenant_slug"],
                role=ws["role"],
                countries=list(ws["countries"] or []),
                country_scope=list(ws["country_scope"]) if ws["country_scope"] else None,
                share_pct=ws["share_pct"],
            )
            for ws in workspaces
        ],
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
    # Registration is open since migration 048: every registration is its own
    # organisation on a free month. The same e-mail cannot register twice, and
    # two operators may call their company the same thing - slugs get a suffix.
    taken = fetch_one(
        conn, "SELECT 1 FROM core.app_user WHERE lower(email) = lower(%s)", (payload.email,)
    )
    if taken:
        raise Conflict(
            "Ese correo ya tiene cuenta. Entra con tu contraseña, o pide que te inviten "
            "a la empresa."
        )

    # The organisation is the invisible holder of the plan and of the
    # companies this person creates. Named after the company when the old
    # one-step flow sends one, after the person otherwise.
    org_name = payload.tenant_name or payload.full_name
    base_slug = slugify(org_name) or "cuenta"
    org = fetch_required(
        conn,
        "INSERT INTO core.org (slug, name) VALUES (%s, %s) RETURNING id",
        (_unique_slug(conn, "core.org", base_slug), org_name),
    )

    # The company is created on its own screen, with its country, unless the
    # caller still sends a name (older flow, tests).
    tenant = None
    if payload.tenant_name:
        tenant = fetch_required(
            conn,
            "INSERT INTO core.tenant (slug, name, org_id) VALUES (%s, %s, %s) RETURNING id, name",
            (_unique_slug(conn, "core.tenant", base_slug), payload.tenant_name, org["id"]),
        )
    # The free month starts now: one company, thirty days (migration 048).
    start_trial(conn, org["id"], days=settings.trial_days)

    try:
        password_hash = hash_password(payload.password)
    except ValueError as exc:
        raise ApiError("weak_password", str(exc)) from exc

    user = fetch_required(
        conn,
        """
        INSERT INTO core.app_user
            (tenant_id, org_id, email, password_hash, full_name, role, is_org_admin)
        VALUES (%s, %s, %s, %s, %s, 'owner', true)
        RETURNING id, tenant_id, org_id, email, role, is_org_admin
        """,
        (
            tenant["id"] if tenant else None, org["id"], payload.email.lower(),
            password_hash, payload.full_name,
        ),
    )

    if tenant:
        execute(
            conn,
            "INSERT INTO core.membership (user_id, tenant_id, role) VALUES (%s, %s, 'owner')",
            (user["id"], tenant["id"]),
        )
    # Whoever registers the deployment runs the holding. Migration 036 backfilled
    # this row for the people who already existed; it has to be written here too,
    # or the very first account is an org admin according to the legacy boolean
    # and nobody at all according to core.org_membership.
    execute(
        conn,
        "INSERT INTO core.org_membership (user_id, org_id, role) VALUES (%s, %s, 'admin') "
        "ON CONFLICT (user_id, org_id) DO NOTHING",
        (user["id"], org["id"]),
    )

    _record_auth_event(
        conn, email=payload.email, event="register", request=request,
        tenant_id=tenant["id"] if tenant else None, user_id=user["id"],
    )
    logger.info("account registered for org %s", org["id"])
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
        SELECT id, tenant_id, org_id, email, role, is_org_admin, password_hash, is_active
        FROM core.app_user WHERE lower(email) = lower(%s)
        """,
        (payload.email,),
    )

    # Same message AND the same work whether the user exists or not: skipping
    # argon2 when there is no row makes "no such user" answer in microseconds
    # and "wrong password" in milliseconds, which is an account enumeration
    # oracle. The dummy hash is verified and its result thrown away.
    stored_hash = user["password_hash"] if user is not None else _DUMMY_HASH
    matched = verify_password(stored_hash, payload.password)
    if user is None or not matched:
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
        SELECT rt.id AS token_id, u.id, u.tenant_id, u.org_id, u.email, u.role,
               u.is_org_admin, u.is_active,
               rt.expires_at, rt.revoked_at, rt.tenant_id AS session_tenant_id
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
        tenant_id=row["session_tenant_id"] or row["tenant_id"], user_id=row["id"],
    )
    # Stay in the company the session was standing in, not the default one.
    return _issue_tokens(conn, settings, row, tenant_id=row["session_tenant_id"])


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
        SELECT u.id, u.email, u.full_name, u.created_at, u.is_org_admin,
               u.org_id, o.name AS org_name,
               t.id AS tenant_id, t.name AS tenant_name
        FROM core.app_user u
        LEFT JOIN core.org o ON o.id = u.org_id
        LEFT JOIN core.tenant t ON t.id = %s
        WHERE u.id = %s
        """,
        (user.tenant_id, user.id),
    )
    if row is None:
        raise NotFound("El usuario del token ya no existe")

    workspaces = _workspaces(conn, user.id)
    org_role = _org_role(conn, user.id)
    row["is_org_admin"] = bool(row.get("is_org_admin")) or org_role == "admin"
    subscription = (
        SubscriptionSummary(**subscription_state(conn, row["org_id"]).as_dict())
        if row.get("org_id")
        else None
    )
    return UserResponse(
        **row,
        subscription=subscription,
        org_role=org_role,
        # The role that applies WHERE THE CALLER IS STANDING, not a global one.
        # Both values were read from columns constrained to these Literals.
        role=cast(Role, user.role),
        countries=list(user.countries) if user.countries else None,
        capabilities=cast(
            "list[Capability]", sorted(CAPABILITIES.get(user.role, frozenset()))
        ),
        workspaces=[
            WorkspaceSummary(
                tenant_id=ws["tenant_id"],
                name=ws["tenant_name"],
                slug=ws["tenant_slug"],
                role=ws["role"],
                countries=list(ws["countries"] or []),
                country_scope=list(ws["country_scope"]) if ws["country_scope"] else None,
                share_pct=ws["share_pct"],
            )
            for ws in workspaces
        ],
    )


# =============================================================================
# The account panel: the part of Data Effi a person administers about themselves.
#
# Everything here is keyed by `user.id` from the token and NEVER by a parameter,
# so there is no id to tamper with: you can only ever read and change your own
# account. That is why these live outside core.membership's reach - they are not
# about a company at all.
# =============================================================================


@router.patch("/me", response_model=UserResponse, summary="Editar mi perfil")
def update_me(
    payload: ProfileUpdateRequest, user: CurrentUserDep, conn: UnscopedDbDep
) -> UserResponse:
    fields = payload.model_dump(exclude_unset=True)
    # Spelled out instead of assembled from the payload keys: this router is not
    # in the S608 allow-list of ruff.toml, and with a single editable column a
    # dynamic SET clause buys nothing. Adding a field here means adding a line.
    if "full_name" in fields:
        execute(
            conn,
            "UPDATE core.app_user SET full_name = %s WHERE id = %s",
            (fields["full_name"], user.id),
        )
    return me(user=user, conn=conn)


@router.post(
    "/me/password",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    dependencies=[Depends(rate_limit("auth", "rate_limit_auth_per_minute"))],
    summary="Cambiar mi contraseña",
)
def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    user: CurrentUserDep,
    conn: UnscopedDbDep,
) -> None:
    """Changing the password ends every OTHER session, which is the point.

    The reason people change a password is that they think someone else has it.
    Leaving the other sessions alive would hand the intruder a working refresh
    token for as long as it lasts, so they are revoked here - including this
    caller's own, because the API cannot tell which refresh token belongs to the
    access token in front of it. The frontend logs in again.
    """
    row = fetch_one(
        conn, "SELECT password_hash FROM core.app_user WHERE id = %s", (user.id,)
    )
    if row is None:
        raise NotFound("El usuario del token ya no existe")

    if not verify_password(row["password_hash"], payload.current_password):
        _record_auth_event(
            conn, email=user.email, event="password_change_refused",
            request=request, user_id=user.id, tenant_id=user.tenant_id,
        )
        raise Unauthorized("La contraseña actual no es correcta")

    if payload.new_password == payload.current_password:
        raise ApiError("same_password", "La contraseña nueva es igual a la actual")

    try:
        new_hash = hash_password(payload.new_password)
    except ValueError as exc:
        raise ApiError("weak_password", str(exc)) from exc

    execute(
        conn, "UPDATE core.app_user SET password_hash = %s WHERE id = %s",
        (new_hash, user.id),
    )
    execute(
        conn,
        "UPDATE core.refresh_token SET revoked_at = now() "
        "WHERE user_id = %s AND revoked_at IS NULL",
        (user.id,),
    )
    _record_auth_event(
        conn, email=user.email, event="password_changed",
        request=request, user_id=user.id, tenant_id=user.tenant_id,
    )


@router.get(
    "/me/sessions", response_model=list[SessionRow], summary="Mis sesiones abiertas"
)
def list_sessions(user: CurrentUserDep, conn: UnscopedDbDep) -> list[SessionRow]:
    """Every refresh token still able to come back, and which company it stands in.

    `is_current` is not reported: an access token carries no reference to the
    refresh token it was minted from, so claiming to know which row is "this
    device" would be a guess. The frontend marks it, since it holds the token.
    """
    rows = fetch_all(
        conn,
        """
        SELECT r.id, r.tenant_id, t.name AS tenant_name, r.created_at, r.expires_at
        FROM core.refresh_token r
        LEFT JOIN core.tenant t ON t.id = r.tenant_id
        WHERE r.user_id = %s AND r.revoked_at IS NULL AND r.expires_at > now()
        ORDER BY r.created_at DESC
        """,
        (user.id,),
    )
    return [SessionRow(**row) for row in rows]


@router.delete(
    "/me/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    response_model=None,
    summary="Cerrar una sesión",
)
def revoke_session(session_id: UUID, user: CurrentUserDep, conn: UnscopedDbDep) -> None:
    revoked = fetch_one(
        conn,
        "UPDATE core.refresh_token SET revoked_at = now() "
        "WHERE id = %s AND user_id = %s AND revoked_at IS NULL "
        "RETURNING id",
        (session_id, user.id),
    )
    if revoked is None:
        # Also the answer when the row belongs to somebody else: a 404 tells the
        # caller nothing about whose session that id is.
        raise NotFound("Esa sesión no existe o ya estaba cerrada")


@router.post(
    "/switch",
    response_model=TokenResponse,
    summary="Cambiar de sociedad sin volver a iniciar sesión",
)
def switch_workspace(
    payload: SwitchWorkspaceRequest,
    conn: UnscopedDbDep,
    settings: SettingsDep,
    user: CurrentUserDep,
) -> TokenResponse:
    """Mint a token for another company this person belongs to.

    The membership is re-read from the database rather than trusted from the
    presented token: a grant revoked five minutes ago must not survive because
    the browser still holds a token minted before it was.
    """
    row = fetch_one(
        conn,
        """
        SELECT u.id, u.tenant_id, u.org_id, u.email, u.role, u.is_org_admin, u.is_active
        FROM core.app_user u WHERE u.id = %s
        """,
        (user.id,),
    )
    if row is None or not row["is_active"]:
        raise Unauthorized("Sesión inválida. Vuelve a iniciar sesión.")

    allowed = {ws["tenant_id"] for ws in _workspaces(conn, user.id)}
    if payload.tenant_id not in allowed:
        raise Forbidden("No tienes acceso a esa empresa")

    return _issue_tokens(conn, settings, row, tenant_id=payload.tenant_id)


@router.post(
    "/invite",
    response_model=InviteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invitar a alguien al workspace",
)
def invite(
    payload: InviteRequest,
    conn: UnscopedDbDep,
    user: Annotated[CurrentUser, Depends(require_cap("manage"))],
) -> InviteResponse:
    """Invite someone into THIS company, with a role and optionally a country scope.

    Two shapes, because a partner in your Guatemala company may already have an
    account from your Colombia one:

      - unknown email  -> an invitation token they redeem to set a password
      - known email    -> the membership is granted immediately, no token, no
                          second password. `already_registered` says which
                          happened so the UI can say "ya puede entrar" instead
                          of handing over a link that does nothing.
    """
    tenant_id = _require_tenant(user)
    email = payload.email.lower()
    scope = list(payload.country_scope) if payload.country_scope else None

    if scope:
        _assert_countries_active(conn, tenant_id, scope)

    person = fetch_one(
        conn, "SELECT id FROM core.app_user WHERE lower(email) = lower(%s)", (email,)
    )

    if person is not None:
        already = fetch_one(
            conn,
            "SELECT 1 FROM core.membership WHERE user_id = %s AND tenant_id = %s AND is_active",
            (person["id"], tenant_id),
        )
        if already:
            raise Conflict("Esa persona ya tiene acceso a esta sociedad")

        execute(
            conn,
            """
            INSERT INTO core.membership
                (user_id, tenant_id, role, country_scope, share_pct, business_model)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, tenant_id) DO UPDATE
                SET role = EXCLUDED.role,
                    country_scope = EXCLUDED.country_scope,
                    share_pct = EXCLUDED.share_pct,
                    business_model = EXCLUDED.business_model,
                    is_active = true
            """,
            (
                person["id"], tenant_id, payload.role, scope, payload.share_pct,
                payload.business_model,
            ),
        )
        logger.info("existing user granted membership on tenant %s", tenant_id)
        return InviteResponse(
            id=person["id"],
            email=email,
            role=payload.role,
            expires_at=datetime.now(UTC),
            invitation_token=None,
            already_registered=True,
            country_scope=scope,
            share_pct=payload.share_pct,
            business_model=payload.business_model,
        )

    # A pending invitation for the same company is replaced, not duplicated:
    # re-inviting someone is how you fix a typo'd role.
    execute(
        conn,
        "DELETE FROM core.invitation "
        "WHERE tenant_id = %s AND lower(email) = lower(%s) AND accepted_at IS NULL",
        (tenant_id, email),
    )

    token, token_hash = generate_invitation_token()
    expires_at = datetime.now(UTC) + timedelta(days=INVITATION_TTL_DAYS)
    row = fetch_required(
        conn,
        """
        INSERT INTO core.invitation
            (tenant_id, email, role, token_hash, invited_by, expires_at, country_scope,
             share_pct, business_model)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        RETURNING id, email, role, expires_at, country_scope, share_pct, business_model
        """,
        (
            tenant_id,
            email,
            payload.role,
            token_hash,
            user.id,
            expires_at,
            scope,
            payload.share_pct,
            payload.business_model,
        ),
    )
    return InviteResponse(**row, invitation_token=token, already_registered=False)


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
        SELECT i.id, i.tenant_id, i.email, i.role, i.expires_at, i.accepted_at,
               i.country_scope, i.share_pct, i.business_model, t.org_id
        FROM core.invitation i
        JOIN core.tenant t ON t.id = i.tenant_id
        WHERE i.token_hash = %s
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

    user = fetch_required(
        conn,
        """
        INSERT INTO core.app_user (tenant_id, org_id, email, password_hash, full_name, role)
        VALUES (%s, %s, %s, %s, %s, %s)
        RETURNING id, tenant_id, org_id, email, role, is_org_admin
        """,
        (
            invitation["tenant_id"],
            invitation["org_id"],
            invitation["email"],
            password_hash,
            payload.full_name,
            invitation["role"],
        ),
    )
    execute(
        conn,
        """
        INSERT INTO core.membership
            (user_id, tenant_id, role, country_scope, share_pct, business_model)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, tenant_id) DO UPDATE
            SET role = EXCLUDED.role,
                country_scope = EXCLUDED.country_scope,
                share_pct = EXCLUDED.share_pct,
                business_model = EXCLUDED.business_model,
                is_active = true
        """,
        (
            user["id"],
            invitation["tenant_id"],
            invitation["role"],
            invitation["country_scope"],
            invitation["share_pct"],
            invitation["business_model"],
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
