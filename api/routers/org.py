"""The holding: several companies, one operator, one consolidated picture.

WHY THIS ROUTER READS EACH COMPANY SEPARATELY
Row-level security keys on `norte.tenant_id`, one company per transaction. The
roll-up below honours that instead of working around it: it asks each company in
its own scoped transaction and adds the results up in Python. A widened policy
would have been fewer lines and would have turned every bug in this file into a
cross-company data leak. Ten companies cost ten short queries, which is cheaper
than the class of incident it avoids.

WHAT "CONSOLIDATED" MEANS HERE
Every figure is converted to the org's base currency (USD unless changed) using
the FX rate `mart.v_global_summary` already carries. A company with no rate for
its currency is NOT counted at 1:1 - it is listed in `unavailable`, because a
total that silently treats quetzales as dollars is worse than one that says
which company is missing.

WHO MAY CALL WHAT
- Roll-up and company list: an org admin sees the whole holding; anyone else
  sees exactly the companies they hold a membership in, added up.
- Creating companies, granting and revoking access: org admin only.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status

from api.billing import subscription_state
from api.db import connection, execute, fetch_all, fetch_one, fetch_required
from api.deps import CurrentUser, CurrentUserDep, UnscopedDbDep
from api.errors import ApiError, Conflict, Forbidden, NotFound, PaymentRequired
from api.schemas import (
    DATE_FIELDS,
    MemberRow,
    MemberUpdateRequest,
    OrgCountryRow,
    OrgMemberGrantRequest,
    OrgMemberRow,
    OrgMemberUpdateRequest,
    OrgRow,
    OrgSummaryResponse,
    OrgTenantRow,
    OrgTotals,
    OrgUpdateRequest,
    SupportedCountry,
    TenantCreateRequest,
    TenantRow,
    TenantUpdateRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/org", tags=["org"])

OptionalDate = Annotated[date | None, Query(description="Formato ISO: yyyy-mm-dd")]


# -----------------------------------------------------------------------------
# Access
# -----------------------------------------------------------------------------


def require_org(user: CurrentUserDep) -> CurrentUser:
    """Anyone who belongs to an organization may look at their own roll-up."""
    if user.org_id is None:
        raise Forbidden("Tu usuario no pertenece a ninguna organización")
    return user


def require_org_admin(user: CurrentUserDep) -> CurrentUser:
    """Creating companies and moving people between them is the operator's call.

    Reads `core.org_membership` through the token claim since migration 036.
    `is_org_admin` is still honoured because a token minted before that
    migration - or by the API version still deployed - carries only the boolean.
    """
    if user.org_id is None or not user.manages_org():
        raise Forbidden("Solo un administrador de la organización puede hacer esto")
    return user


OrgUser = Annotated[CurrentUser, Depends(require_org)]
OrgAdmin = Annotated[CurrentUser, Depends(require_org_admin)]


def _org_id(user: CurrentUser) -> UUID:
    """The org an `OrgAdmin` administers, as a plain UUID.

    `require_org_admin` has already refused anyone without one; this only tells
    the type checker so, at the call sites that pass it on.
    """
    if user.org_id is None:
        raise Forbidden("Tu usuario no pertenece a ninguna organización.")
    return user.org_id


def _visible_tenants(conn, user: CurrentUser) -> list[dict]:
    """Which companies this caller may consolidate.

    Anyone with a role over the holding - admin, analyst or viewer - gets every
    company in it. Everyone else gets exactly the companies they hold a membership
    in, which is what makes "the global is mine" true without giving a partner a
    window into the other partnerships.

    Note what this widens and what it does not: the roll-up asks each of these
    companies IN ITS OWN SCOPED TRANSACTION, so an org viewer reads totals through
    the same row-level security as everybody else. It buys them no way whatsoever
    to stand inside a company they do not belong to - `/auth/switch` still refuses.
    """
    if user.reads_org():
        return fetch_all(
            conn,
            "SELECT tenant_id, tenant_slug, tenant_name FROM core.org_tenants(%s)",
            (user.org_id,),
        )
    return fetch_all(
        conn,
        """
        SELECT w.tenant_id, w.tenant_slug, w.tenant_name
        FROM core.user_workspaces(%s) w
        WHERE w.org_id = %s
        ORDER BY w.tenant_name
        """,
        (user.id, user.org_id),
    )


def _scope_to(conn, tenant_id: UUID) -> None:
    """Point THIS transaction at one company, for the tables under RLS.

    These endpoints run on an unscoped connection because they read core.tenant
    and core.membership, which have no policy and are queried before any company
    is chosen. `core.workspace_country` does have one, so reading or writing it
    needs a context - and it has to be this same transaction, not a second
    connection: a company inserted a few lines earlier is not visible to anyone
    else until this transaction commits, which is exactly the foreign key
    violation a second connection produces.

    `SET LOCAL` dies with the transaction, so nothing leaks into the next
    request that borrows this pooled connection.
    """
    execute(conn, "SELECT set_config('norte.tenant_id', %s, true)", (str(tenant_id),))


def _active_countries(conn, tenant_id: UUID) -> list[str]:
    _scope_to(conn, tenant_id)
    rows = fetch_all(
        conn,
        "SELECT country_code FROM core.workspace_country "
        "WHERE tenant_id = %s AND is_active ORDER BY country_code",
        (tenant_id,),
    )
    return [row["country_code"].strip().upper() for row in rows]


def _grants(conn, user: CurrentUser) -> dict[UUID, dict]:
    """This person's own scope and stake per company, for filtering and 'my share'."""
    rows = fetch_all(
        conn,
        "SELECT tenant_id, country_scope, share_pct FROM core.user_workspaces(%s)",
        (user.id,),
    )
    return {row["tenant_id"]: row for row in rows}


# -----------------------------------------------------------------------------
# The consolidated picture
# -----------------------------------------------------------------------------


def _to_usd(value: float | None, fx: float | None) -> float | None:
    if value is None or fx is None:
        return None
    return float(value) * float(fx)


def _read_company(
    tenant_id: UUID,
    *,
    date_from: date | None,
    date_to: date | None,
    date_field: str,
    scope: tuple[str, ...] | None,
) -> list[dict]:
    """One company's per-country totals, read inside its own tenant context."""
    with connection(tenant_id) as conn:
        rows = fetch_all(
            conn,
            "SELECT * FROM mart.f_global_summary(%(date_from)s, %(date_to)s, %(date_field)s)",
            {"date_from": date_from, "date_to": date_to, "date_field": date_field},
        )
    if scope is None:
        return rows
    return [r for r in rows if str(r["country_code"]).upper() in scope]


@router.get(
    "/summary",
    response_model=OrgSummaryResponse,
    summary="Consolidado de todas las sociedades",
)
def summary(
    conn: UnscopedDbDep,
    user: OrgUser,
    date_from: OptionalDate = None,
    date_to: OptionalDate = None,
    date_field: Annotated[str, Query(description="creacion | despacho | entrega")] = "creacion",
) -> OrgSummaryResponse:
    """Add up every company the caller may see, in the org's base currency."""
    if date_field not in DATE_FIELDS:
        raise ApiError(
            "invalid_date_field",
            f"'{date_field}' no es un campo de fecha válido. "
            f"Usa uno de: {', '.join(DATE_FIELDS)}.",
        )
    if date_from and date_to and date_from > date_to:
        raise ApiError(
            "invalid_date_range",
            f"El rango está invertido: 'desde' ({date_from:%Y-%m-%d}) es posterior "
            f"a 'hasta' ({date_to:%Y-%m-%d}).",
        )

    org = fetch_one(
        conn, "SELECT id, name, base_currency FROM core.org WHERE id = %s", (user.org_id,)
    )
    if org is None:
        raise NotFound("Esa organización ya no existe")

    tenants = _visible_tenants(conn, user)
    grants = _grants(conn, user)

    by_tenant: list[OrgTenantRow] = []
    countries: dict[str, OrgCountryRow] = {}
    unavailable: list[str] = []
    totals = OrgTotals()
    revenue_total = ad_spend_total = contribution_total = my_share_total = 0.0
    saw_money = False

    for tenant in tenants:
        tenant_id = tenant["tenant_id"]
        grant = grants.get(tenant_id)

        # A role over the holding reads every company, including ones the person
        # holds no membership in - that is the whole point of `analyst` and
        # `viewer` at the org level, and of being the operator for `admin`.
        # Anyone else only ever adds up the companies they actually belong to.
        if grant is None and not user.reads_org():
            continue

        scope = (
            tuple(c.upper() for c in grant["country_scope"])
            if grant and grant["country_scope"]
            else None
        )

        try:
            rows = _read_company(
                tenant_id,
                date_from=date_from,
                date_to=date_to,
                date_field=date_field,
                scope=scope,
            )
        except Exception:      # one broken company must not blank the holding
            logger.exception("could not read tenant %s for the org roll-up", tenant_id)
            unavailable.append(tenant["tenant_name"])
            continue

        t_shipments = t_delivered = 0
        t_revenue = t_ads = t_contribution = 0.0
        t_fx_missing = False
        t_last: date | None = None

        # Which countries the company OPERATES, not which ones happen to have
        # data in this date range. A company set up yesterday shows "GT, HN"
        # rather than "sin países", which is what the roll-up would say if this
        # were read off the result rows.
        t_countries = [
            code
            for code in _active_countries(conn, tenant_id)
            if scope is None or code in scope
        ]

        for row in rows:
            fx = row["fx_rate_to_usd"]
            t_shipments += int(row["shipments"] or 0)
            t_delivered += int(row["delivered"] or 0)
            if row["country_code"] not in t_countries:
                t_countries.append(row["country_code"])

            if row["fx_missing"] or fx is None:
                t_fx_missing = True
            else:
                revenue_usd = _to_usd(row["revenue"], fx) or 0.0
                ads_usd = _to_usd(row["ad_spend"], fx) or 0.0
                contribution_usd = float(row["contribution_usd"] or 0.0)
                t_revenue += revenue_usd
                t_ads += ads_usd
                t_contribution += contribution_usd
                saw_money = True

                agg = countries.get(row["country_code"])
                if agg is None:
                    agg = OrgCountryRow(
                        country_code=row["country_code"],
                        country_name=row["country_name"],
                        revenue_usd=0.0,
                        contribution_usd=0.0,
                    )
                    countries[row["country_code"]] = agg
                agg.shipments += int(row["shipments"] or 0)
                agg.delivered += int(row["delivered"] or 0)
                agg.revenue_usd = (agg.revenue_usd or 0.0) + revenue_usd
                agg.contribution_usd = (agg.contribution_usd or 0.0) + contribution_usd
                if tenant["tenant_name"] not in agg.tenants:
                    agg.tenants.append(tenant["tenant_name"])

            last = row["last_shipment_date"]
            if last and (t_last is None or last > t_last):
                t_last = last

        share = float(grant["share_pct"]) if grant and grant["share_pct"] else None
        my_share = t_contribution * share / 100 if share is not None else None

        by_tenant.append(
            OrgTenantRow(
                tenant_id=tenant_id,
                name=tenant["tenant_name"],
                slug=tenant["tenant_slug"],
                countries=sorted(set(t_countries)),
                shipments=t_shipments,
                delivered=t_delivered,
                delivery_rate_pct=(
                    round(t_delivered * 100 / t_shipments, 2) if t_shipments else None
                ),
                revenue_usd=round(t_revenue, 2),
                ad_spend_usd=round(t_ads, 2),
                contribution_usd=round(t_contribution, 2),
                share_pct=share,
                my_share_usd=round(my_share, 2) if my_share is not None else None,
                fx_missing=t_fx_missing,
                last_shipment_date=t_last,
            )
        )

        totals.shipments += t_shipments
        totals.delivered += t_delivered
        revenue_total += t_revenue
        ad_spend_total += t_ads
        contribution_total += t_contribution
        if my_share is not None:
            my_share_total += my_share

    for agg in countries.values():
        agg.delivery_rate_pct = (
            round(agg.delivered * 100 / agg.shipments, 2) if agg.shipments else None
        )
        agg.revenue_usd = round(agg.revenue_usd or 0.0, 2)
        agg.contribution_usd = round(agg.contribution_usd or 0.0, 2)

    if saw_money:
        totals.revenue_usd = round(revenue_total, 2)
        totals.ad_spend_usd = round(ad_spend_total, 2)
        totals.contribution_usd = round(contribution_total, 2)
        totals.my_share_usd = round(my_share_total, 2) if my_share_total else None
    if totals.shipments:
        totals.delivery_rate_pct = round(totals.delivered * 100 / totals.shipments, 2)

    return OrgSummaryResponse(
        org_id=org["id"],
        org_name=org["name"],
        base_currency=org["base_currency"],
        date_from=date_from,
        date_to=date_to,
        date_basis=date_field,          # type: ignore[arg-type]
        totals=totals,
        by_tenant=sorted(by_tenant, key=lambda r: (r.contribution_usd or 0.0), reverse=True),
        by_country=sorted(
            countries.values(), key=lambda r: (r.contribution_usd or 0.0), reverse=True
        ),
        unavailable=unavailable,
    )


# -----------------------------------------------------------------------------
# Companies
# -----------------------------------------------------------------------------


# =============================================================================
# People at the organisation level.
#
# WHAT THIS GRANTS, AND WHAT IT DOES NOT
# `admin` runs the holding: creates companies, and hands out these very roles.
# `analyst` and `viewer` see the consolidated roll-up of every company in the
# org - the totals, and nothing underneath them. None of the three opens a
# single order, customer or upload: `core.membership` decides that, per company
# and per country, and it is not touched here.
#
# So "let my partner see the whole group" is one row in core.org_membership, and
# it stays true when a company is added next month - which is the thing that was
# impossible when the answer was a boolean called is_org_admin.
# =============================================================================


def _org_member_row(conn, org_id: UUID, user_id: UUID) -> OrgMemberRow:
    row = fetch_one(
        conn,
        """
        SELECT u.id AS user_id, u.email, u.full_name, m.role, m.is_active,
               u.last_login_at
        FROM core.org_membership m
        JOIN core.app_user u ON u.id = m.user_id
        WHERE m.org_id = %s AND m.user_id = %s
        """,
        (org_id, user_id),
    )
    if row is None:
        raise NotFound("Esa persona no tiene un rol en tu organización")
    return OrgMemberRow(**row)


def _assert_not_last_org_admin(conn, org_id: UUID, user_id: UUID) -> None:
    """An org with no administrator can never grant anything again.

    Every other role can be revoked freely; this one would lock the holding out
    of its own settings, and no endpoint could undo it.
    """
    remaining = fetch_one(
        conn,
        """
        SELECT count(*) AS n
        FROM core.org_membership
        WHERE org_id = %s AND role = 'admin' AND is_active AND user_id <> %s
        """,
        (org_id, user_id),
    )
    if not remaining or remaining["n"] == 0:
        raise Conflict(
            "Es el único administrador de la organización. "
            "Nombra a otro antes de quitarle el rol."
        )


@router.get(
    "/members",
    response_model=list[OrgMemberRow],
    summary="Personas con rol en la organización",
)
def list_org_members(conn: UnscopedDbDep, user: OrgUser) -> list[OrgMemberRow]:
    rows = fetch_all(
        conn,
        """
        SELECT u.id AS user_id, u.email, u.full_name, m.role, m.is_active,
               u.last_login_at
        FROM core.org_membership m
        JOIN core.app_user u ON u.id = m.user_id
        WHERE m.org_id = %s
        ORDER BY u.full_name NULLS LAST, u.email
        """,
        (user.org_id,),
    )
    return [OrgMemberRow(**row) for row in rows]


@router.post(
    "/members",
    response_model=OrgMemberRow,
    status_code=status.HTTP_201_CREATED,
    summary="Dar acceso de organización a alguien",
)
def grant_org_role(
    payload: OrgMemberGrantRequest, conn: UnscopedDbDep, user: OrgAdmin
) -> OrgMemberRow:
    """The person must already exist. This is a promotion, not an invitation.

    Creating an account is `/auth/invite`, and it belongs to a company: someone
    invited straight into the holding would have a login and no reason to use it.
    """
    target = fetch_one(
        conn,
        "SELECT id, org_id FROM core.app_user WHERE lower(email) = lower(%s)",
        (payload.email,),
    )
    if target is None:
        raise NotFound(
            f"No existe una cuenta con el correo {payload.email}. "
            "Invítala primero a una sociedad."
        )
    if target["org_id"] != user.org_id:
        # Deliberately the same answer as "does not exist": whether an email
        # belongs to another holding is not this caller's business.
        raise NotFound(
            f"No existe una cuenta con el correo {payload.email}. "
            "Invítala primero a una sociedad."
        )

    execute(
        conn,
        """
        INSERT INTO core.org_membership (user_id, org_id, role)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, org_id) DO UPDATE
            SET role = EXCLUDED.role, is_active = true
        """,
        (target["id"], user.org_id, payload.role),
    )
    return _org_member_row(conn, user.org_id, target["id"])


@router.patch(
    "/members/{user_id}",
    response_model=OrgMemberRow,
    summary="Cambiar el rol de organización de alguien",
)
def update_org_member(
    user_id: UUID, payload: OrgMemberUpdateRequest, conn: UnscopedDbDep, user: OrgAdmin
) -> OrgMemberRow:
    current = _org_member_row(conn, _org_id(user), user_id)

    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        return current

    losing_admin = (
        fields.get("role", current.role) != "admin" or fields.get("is_active", True) is False
    )
    if current.role == "admin" and losing_admin:
        _assert_not_last_org_admin(conn, _org_id(user), user_id)

    if "role" in fields:
        execute(
            conn,
            "UPDATE core.org_membership SET role = %s WHERE org_id = %s AND user_id = %s",
            (fields["role"], _org_id(user), user_id),
        )
    if "is_active" in fields:
        execute(
            conn,
            "UPDATE core.org_membership SET is_active = %s WHERE org_id = %s AND user_id = %s",
            (fields["is_active"], _org_id(user), user_id),
        )
    return _org_member_row(conn, _org_id(user), user_id)


@router.delete(
    "/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Quitar el acceso de organización",
)
def revoke_org_role(user_id: UUID, conn: UnscopedDbDep, user: OrgAdmin) -> None:
    """Removes the holding-wide role only. Their companies are untouched.

    Someone who is owner of two companies and analyst of the org keeps both
    companies after this - they simply stop seeing the consolidated picture.
    """
    member = _org_member_row(conn, _org_id(user), user_id)
    if member.role == "admin":
        _assert_not_last_org_admin(conn, _org_id(user), user_id)

    execute(
        conn,
        "DELETE FROM core.org_membership WHERE org_id = %s AND user_id = %s",
        (_org_id(user), user_id),
    )


@router.patch("/", response_model=OrgRow, summary="Editar la organización")
def update_org(payload: OrgUpdateRequest, conn: UnscopedDbDep, user: OrgAdmin) -> OrgRow:
    fields = payload.model_dump(exclude_unset=True)

    if "name" in fields:
        execute(
            conn, "UPDATE core.org SET name = %s WHERE id = %s", (fields["name"], user.org_id)
        )
    if "base_currency" in fields:
        # The currency the roll-up converts INTO. Changing it does not touch a
        # single company's own numbers, which stay in the currency they billed.
        execute(
            conn,
            "UPDATE core.org SET base_currency = %s WHERE id = %s",
            (fields["base_currency"].upper(), user.org_id),
        )

    row = fetch_one(
        conn,
        "SELECT id, slug, name, base_currency, is_active FROM core.org WHERE id = %s",
        (user.org_id,),
    )
    if row is None:
        raise NotFound("Tu organización ya no existe")
    return OrgRow(**row)


@router.get("/tenants", response_model=list[TenantRow], summary="Sociedades de la organización")
def list_tenants(conn: UnscopedDbDep, user: OrgUser) -> list[TenantRow]:
    tenants = _visible_tenants(conn, user)
    if not tenants:
        return []

    ids = [t["tenant_id"] for t in tenants]
    detail = {
        row["id"]: row
        for row in fetch_all(
            conn,
            """
            SELECT t.id, t.notes, t.company_type, t.created_at,
                   (SELECT count(*) FROM core.membership m
                     WHERE m.tenant_id = t.id AND m.is_active) AS member_count
            FROM core.tenant t WHERE t.id = ANY(%s)
            """,
            (ids,),
        )
    }

    rows: list[TenantRow] = []
    for tenant in tenants:
        extra = detail.get(tenant["tenant_id"], {})
        rows.append(
            TenantRow(
                tenant_id=tenant["tenant_id"],
                name=tenant["tenant_name"],
                slug=tenant["tenant_slug"],
                countries=_active_countries(conn, tenant["tenant_id"]),
                member_count=int(extra.get("member_count", 0) or 0),
                notes=extra.get("notes"),
                company_type=extra.get("company_type"),
                created_at=extra["created_at"],
            )
        )
    return rows


@router.post(
    "/tenants",
    response_model=TenantRow,
    status_code=status.HTTP_201_CREATED,
    summary="Crear una sociedad",
)
def create_tenant(payload: TenantCreateRequest, conn: UnscopedDbDep, user: OrgAdmin) -> TenantRow:
    """A new company inside the holding, with the operator as its owner.

    The creator gets an owner membership immediately: a company nobody can enter
    is a support ticket waiting to happen.
    """
    from api.routers.auth import _unique_slug, slugify

    base_slug = slugify(payload.name)
    if not base_slug:
        raise ApiError("invalid_name", "Ese nombre no produce un identificador válido")
    # Two operators may both call their company "Distrilatam Ecuador": the slug
    # is an internal handle, never the identity, so it simply gets a suffix.
    slug = _unique_slug(conn, "core.tenant", base_slug)

    # The plan says how many companies the organisation may hold (048). The
    # free month holds one; a requested plan counts only once it is active.
    state = subscription_state(conn, _org_id(user))
    if state.max_tenants is not None and state.tenants_used >= state.max_tenants:
        raise PaymentRequired(
            f"Tu plan permite {state.max_tenants} "
            f"{'empresa' if state.max_tenants == 1 else 'empresas'} y ya tienes "
            f"{state.tenants_used}. Elige un plan más grande para crear otra.",
            code="plan_limit",
            detail={"max_tenants": state.max_tenants, "tenants_used": state.tenants_used},
        )

    if payload.countries:
        known = fetch_all(
            conn,
            "SELECT code FROM core.country WHERE code = ANY(%s) AND is_supported",
            ([c.upper() for c in payload.countries],),
        )
        missing = {c.upper() for c in payload.countries} - {r["code"] for r in known}
        if missing:
            raise ApiError("unknown_country", f"País no soportado: {', '.join(sorted(missing))}")

    tenant = fetch_required(
        conn,
        "INSERT INTO core.tenant (slug, name, org_id, notes, company_type) "
        "VALUES (%s, %s, %s, %s, %s) RETURNING id, slug, name, notes, company_type, created_at",
        (slug, payload.name, user.org_id, payload.notes, payload.company_type),
    )

    execute(
        conn,
        "INSERT INTO core.membership (user_id, tenant_id, role) VALUES (%s, %s, 'owner') "
        "ON CONFLICT (user_id, tenant_id) DO NOTHING",
        (user.id, tenant["id"]),
    )

    # Written in this same transaction, scoped to the new company: the tenant row
    # above is not visible to any other connection until this commits.
    if payload.countries:
        _scope_to(conn, tenant["id"])
        for code in payload.countries:
            execute(
                conn,
                "INSERT INTO core.workspace_country (tenant_id, country_code) "
                "VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (tenant["id"], code.upper()),
            )

    logger.info("tenant %s created in org %s", tenant["id"], user.org_id)
    return TenantRow(
        tenant_id=tenant["id"],
        name=tenant["name"],
        slug=tenant["slug"],
        countries=[c.upper() for c in payload.countries],
        member_count=1,
        notes=tenant["notes"],
        company_type=tenant["company_type"],
        created_at=tenant["created_at"],
    )


@router.patch("/tenants/{tenant_id}", response_model=TenantRow, summary="Editar una empresa")
def update_tenant(
    tenant_id: UUID, payload: TenantUpdateRequest, conn: UnscopedDbDep, user: OrgAdmin
) -> TenantRow:
    """Rename a company, change its notes, or change the countries it operates in.

    Countries are set as a whole list, not toggled one by one: the org chart
    shows "this company operates in CO and EC", and saving that picture is one
    call. A country dropped from the list is DEACTIVATED, never deleted - its
    shipments stay, its maturation window stays, and turning it back on brings
    everything back exactly as it was.
    """
    _assert_tenant_in_org(conn, user, tenant_id)
    fields = payload.model_dump(exclude_unset=True)

    if "name" in fields:
        execute(
            conn, "UPDATE core.tenant SET name = %s WHERE id = %s", (fields["name"], tenant_id)
        )
    if "notes" in fields:
        execute(
            conn, "UPDATE core.tenant SET notes = %s WHERE id = %s", (fields["notes"], tenant_id)
        )
    if fields.get("company_type"):
        execute(
            conn,
            "UPDATE core.tenant SET company_type = %s WHERE id = %s",
            (fields["company_type"], tenant_id),
        )

    if "countries" in fields:
        wanted = sorted({c.upper() for c in payload.countries or []})
        if wanted:
            known = fetch_all(
                conn,
                "SELECT code FROM core.country WHERE code = ANY(%s) AND is_supported",
                (wanted,),
            )
            missing = set(wanted) - {r["code"] for r in known}
            if missing:
                raise ApiError(
                    "unknown_country", f"País no soportado: {', '.join(sorted(missing))}"
                )

        _scope_to(conn, tenant_id)
        for code in wanted:
            execute(
                conn,
                """
                INSERT INTO core.workspace_country (tenant_id, country_code, is_active)
                VALUES (%s, %s, true)
                ON CONFLICT (tenant_id, country_code) DO UPDATE SET is_active = true
                """,
                (tenant_id, code),
            )
        execute(
            conn,
            "UPDATE core.workspace_country SET is_active = false "
            "WHERE tenant_id = %s AND is_active AND NOT (country_code = ANY(%s))",
            (tenant_id, wanted),
        )
        logger.info("tenant %s now operates in %s", tenant_id, wanted or "no country")

    return _tenant_row(conn, tenant_id)


def _tenant_row(conn, tenant_id: UUID) -> TenantRow:
    row = fetch_required(
        conn,
        """
        SELECT t.id, t.name, t.slug, t.notes, t.company_type, t.created_at,
               (SELECT count(*) FROM core.membership m
                 WHERE m.tenant_id = t.id AND m.is_active) AS member_count
        FROM core.tenant t WHERE t.id = %s
        """,
        (tenant_id,),
    )
    return TenantRow(
        tenant_id=row["id"],
        name=row["name"],
        slug=row["slug"],
        countries=_active_countries(conn, tenant_id),
        member_count=int(row["member_count"] or 0),
        notes=row["notes"],
        company_type=row["company_type"],
        created_at=row["created_at"],
    )


@router.get(
    "/countries",
    response_model=list[SupportedCountry],
    summary="Países en los que la plataforma puede operar",
)
def supported_countries(conn: UnscopedDbDep, user: OrgUser) -> list[SupportedCountry]:
    """The catalogue, for choosing where a company operates.

    Not `/config/countries`: that one answers for the company the caller is
    standing in, and an org admin editing the chart may be standing in none.
    """
    rows = fetch_all(
        conn,
        "SELECT code, name, currency_code FROM core.country WHERE is_supported ORDER BY name",
    )
    return [SupportedCountry(**row) for row in rows]


# -----------------------------------------------------------------------------
# People
# -----------------------------------------------------------------------------


def _assert_tenant_in_org(conn, user: CurrentUser, tenant_id: UUID) -> None:
    row = fetch_one(
        conn,
        "SELECT 1 FROM core.tenant WHERE id = %s AND org_id = %s",
        (tenant_id, user.org_id),
    )
    if row is None:
        raise NotFound("Esa sociedad no existe en tu organización")


def _member_row(conn, tenant_id: UUID, user_id: UUID) -> MemberRow:
    row = fetch_one(
        conn,
        """
        SELECT u.id AS user_id, u.email, u.full_name, m.role, m.country_scope,
               m.share_pct, m.business_model, m.tenant_id, t.name AS tenant_name,
               m.is_active, u.last_login_at
        FROM core.membership m
        JOIN core.app_user u ON u.id = m.user_id
        JOIN core.tenant t ON t.id = m.tenant_id
        WHERE m.user_id = %s AND m.tenant_id = %s
        """,
        (user_id, tenant_id),
    )
    if row is None:
        raise NotFound("Esa persona no tiene acceso a esta sociedad")
    return MemberRow(
        user_id=row["user_id"],
        email=row["email"],
        full_name=row["full_name"],
        role=row["role"],
        country_scope=list(row["country_scope"]) if row["country_scope"] else None,
        share_pct=float(row["share_pct"]) if row["share_pct"] is not None else None,
        business_model=row["business_model"],
        tenant_id=row["tenant_id"],
        tenant_name=row["tenant_name"],
        is_active=row["is_active"],
        last_login_at=row["last_login_at"],
    )


def _assert_not_last_owner(conn, tenant_id: UUID, current_role: str) -> None:
    """A company with no active owner is a company nobody can administer."""
    if current_role != "owner":
        return
    owners = fetch_one(
        conn,
        "SELECT count(*) AS n FROM core.membership "
        "WHERE tenant_id = %s AND role = 'owner' AND is_active",
        (tenant_id,),
    )
    if owners and owners["n"] <= 1:
        raise Conflict(
            "Esta sociedad quedaría sin propietario. Nombra otro antes de cambiar este."
        )


@router.get(
    "/tenants/{tenant_id}/members",
    response_model=list[MemberRow],
    summary="Quién tiene acceso a una sociedad",
)
def list_members(tenant_id: UUID, conn: UnscopedDbDep, user: OrgUser) -> list[MemberRow]:
    _assert_tenant_in_org(conn, user, tenant_id)

    # An owner of that company may see its people; anyone else needs to manage
    # the org. Note this is `manages_org`, not `reads_org`: an org analyst reads
    # the consolidated numbers and still has no business enumerating who works
    # at a company they do not belong to.
    if not user.manages_org():
        mine = fetch_one(
            conn,
            "SELECT role FROM core.membership "
            "WHERE user_id = %s AND tenant_id = %s AND is_active",
            (user.id, tenant_id),
        )
        if mine is None or mine["role"] != "owner":
            raise Forbidden("Solo el propietario de la sociedad puede ver sus usuarios")

    rows = fetch_all(
        conn,
        """
        SELECT u.id AS user_id, u.email, u.full_name, m.role, m.country_scope,
               m.share_pct, m.business_model, m.tenant_id, t.name AS tenant_name,
               m.is_active, u.last_login_at
        FROM core.membership m
        JOIN core.app_user u ON u.id = m.user_id
        JOIN core.tenant t ON t.id = m.tenant_id
        WHERE m.tenant_id = %s
        ORDER BY u.email
        """,
        (tenant_id,),
    )
    return [
        MemberRow(
            user_id=row["user_id"],
            email=row["email"],
            full_name=row["full_name"],
            role=row["role"],
            country_scope=list(row["country_scope"]) if row["country_scope"] else None,
            share_pct=float(row["share_pct"]) if row["share_pct"] is not None else None,
            business_model=row["business_model"],
            tenant_id=row["tenant_id"],
            tenant_name=row["tenant_name"],
            is_active=row["is_active"],
            last_login_at=row["last_login_at"],
        )
        for row in rows
    ]


@router.patch(
    "/tenants/{tenant_id}/members/{user_id}",
    response_model=MemberRow,
    summary="Cambiar el rol, los países o la participación de alguien",
)
def update_member(
    tenant_id: UUID,
    user_id: UUID,
    payload: MemberUpdateRequest,
    conn: UnscopedDbDep,
    user: OrgAdmin,
) -> MemberRow:
    _assert_tenant_in_org(conn, user, tenant_id)

    membership = fetch_one(
        conn,
        "SELECT id, role FROM core.membership WHERE user_id = %s AND tenant_id = %s",
        (user_id, tenant_id),
    )
    if membership is None:
        raise NotFound("Esa persona no tiene acceso a esta sociedad")

    if (payload.role and payload.role != "owner") or payload.is_active is False:
        _assert_not_last_owner(conn, tenant_id, membership["role"])

    if payload.country_scope:
        active = set(_active_countries(conn, tenant_id))
        unknown = [c for c in payload.country_scope if c.upper() not in active]
        if unknown:
            raise ApiError(
                "unknown_country", f"Esta sociedad no opera en: {', '.join(unknown)}."
            )

    if not any(
        value is not None
        for value in (
            payload.role, payload.country_scope, payload.share_pct,
            payload.business_model, payload.is_active,
        )
    ) and not payload.clear_country_scope:
        raise ApiError("nothing_to_update", "No enviaste ningún cambio")

    # One fixed statement rather than a SET clause assembled from whichever
    # fields arrived: COALESCE leaves an omitted field exactly as it was, and
    # `clear_country_scope` is the explicit way to say NULL - which COALESCE
    # alone cannot express, since "not sent" and "set to nothing" would
    # otherwise look identical.
    execute(
        conn,
        """
        UPDATE core.membership SET
            role          = COALESCE(%(role)s, role),
            country_scope = CASE
                                WHEN %(clear_scope)s THEN NULL
                                ELSE COALESCE(%(scope)s, country_scope)
                            END,
            share_pct     = COALESCE(%(share)s, share_pct),
            business_model = COALESCE(%(business_model)s, business_model),
            is_active     = COALESCE(%(active)s, is_active)
        WHERE user_id = %(user_id)s AND tenant_id = %(tenant_id)s
        """,
        {
            "role": payload.role,
            "business_model": payload.business_model,
            "clear_scope": payload.clear_country_scope,
            "scope": [c.upper() for c in payload.country_scope]
            if payload.country_scope
            else None,
            "share": payload.share_pct,
            "active": payload.is_active,
            "user_id": user_id,
            "tenant_id": tenant_id,
        },
    )

    # Any token minted before this moment still carries the old grant. Killing
    # the refresh tokens for that company forces a new one within the access
    # token's lifetime instead of at the end of the fortnight.
    execute(
        conn,
        "UPDATE core.refresh_token SET revoked_at = now() "
        "WHERE user_id = %s AND tenant_id = %s AND revoked_at IS NULL",
        (user_id, tenant_id),
    )

    return _member_row(conn, tenant_id, user_id)


@router.delete(
    "/tenants/{tenant_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Quitarle el acceso a una sociedad",
)
def remove_member(tenant_id: UUID, user_id: UUID, conn: UnscopedDbDep, user: OrgAdmin) -> None:
    """Revoke the grant. The person and their other companies are untouched."""
    _assert_tenant_in_org(conn, user, tenant_id)

    membership = fetch_one(
        conn,
        "SELECT role FROM core.membership WHERE user_id = %s AND tenant_id = %s",
        (user_id, tenant_id),
    )
    if membership is None:
        raise NotFound("Esa persona no tiene acceso a esta sociedad")

    _assert_not_last_owner(conn, tenant_id, membership["role"])

    execute(
        conn,
        "DELETE FROM core.membership WHERE user_id = %s AND tenant_id = %s",
        (user_id, tenant_id),
    )
    execute(
        conn,
        "UPDATE core.refresh_token SET revoked_at = now() "
        "WHERE user_id = %s AND tenant_id = %s AND revoked_at IS NULL",
        (user_id, tenant_id),
    )
    logger.info("membership revoked: user %s on tenant %s", user_id, tenant_id)
