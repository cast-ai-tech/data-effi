"""Logging in to Effi, and the permission checklist that follows.

WHAT THESE TESTS ARE ACTUALLY DEFENDING
---------------------------------------
Not "does the code work". The behaviours below are the ones where doing the
convenient thing harms a real merchant:

  a retry after a wrong password        locks them out of their own Effi
  a lockout treated as a soft error     turns one lockout into a permanent one
  a 403 reported as "HTTP 403"          becomes a support ticket instead of a
                                        two-minute fix they do themselves
  a guessed login shipped to production the first customer discovers it

Every test here has a corresponding way for a well-meaning refactor to break it,
which is why they assert on behaviour rather than on call counts.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from connectors.effi import auth
from connectors.effi.auth import (
    AccountLocked,
    EffiAuthenticator,
    EffiSession,
    InvalidCredentials,
    LoginContractUnverified,
)
from connectors.effi.permissions import PERMISSION_PROBES, run_preflight
from connectors.effi.session_fetcher import SessionExpiredError
from pipeline.vault import Credential

CREDENTIAL = Credential(username="reportes@tienda.co", password="clave-de-prueba")


# =============================================================================
# The login contract is not shipped as a guess
# =============================================================================


def test_login_refuses_to_run_while_the_contract_is_unverified():
    """Until the real panel is inspected, logging in must fail closed.

    The failure message has to offer the alternative, because there IS one: the
    merchant uploads the export by hand and gets the identical dashboard. A
    dead end with no alternative would be worse than the guess.
    """
    assert auth.LOGIN_CONTRACT_VERIFIED is False, (
        "Si esto ya es True, el login se verificó contra una cuenta real: "
        "borra este test y deja los de abajo."
    )

    with pytest.raises(LoginContractUnverified) as exc:
        EffiAuthenticator().login(CREDENTIAL)

    assert "a mano" in str(exc.value)


# =============================================================================
# A rejected password is terminal. This is the important one.
# =============================================================================


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "", cookies=None):
        self.status_code = status_code
        self.text = text
        self.cookies = cookies or {}
        self.headers = {}


@pytest.mark.parametrize("status_code", [401, 403])
def test_rejected_credentials_raise_and_do_not_retry(status_code):
    """One attempt, one answer. No second try, ever.

    A retry loop against a wrong password is how a merchant gets locked out of
    the platform they run their business on - by us, on their behalf, while they
    sleep. There is no rate limit gentle enough to make that acceptable.
    """
    authenticator = EffiAuthenticator()
    with pytest.raises(InvalidCredentials):
        authenticator._raise_for_login_status(_FakeResponse(status_code))


def test_a_200_that_re_renders_the_login_form_is_still_a_rejection():
    """Server-rendered panels answer a failed login with 200 and an error page.

    Trusting the status code alone would store a "session" that is really the
    login page, and every sync would then fail with something unrelated.
    """
    body = "<html><body>Usuario o contraseña incorrectos</body></html>"
    with pytest.raises(InvalidCredentials):
        EffiAuthenticator()._raise_for_login_status(_FakeResponse(200, body))


def test_lockout_is_recognised_and_is_not_a_credential_problem():
    """`locked` must not be reported as `invalid`.

    They lead to opposite advice. `invalid` tells the merchant to retype their
    password - which, against a locked account, does nothing and may extend the
    lockout. `locked` tells them to go unlock it, which is the only thing that
    works.
    """
    body = "<html>Cuenta bloqueada por demasiados intentos</html>"
    with pytest.raises(AccountLocked):
        EffiAuthenticator()._raise_for_login_status(_FakeResponse(200, body))


def test_success_page_is_not_mistaken_for_a_rejection():
    """The rejection markers are narrow on purpose.

    A false positive would tell a merchant their password is wrong when it is
    not, and they would change a password that was working.
    """
    body = "<html><body>Bienvenido a Effi. Reportes disponibles.</body></html>"
    EffiAuthenticator()._raise_for_login_status(_FakeResponse(200, body))  # no raise


# =============================================================================
# A stored session is reused; a stale one is not
# =============================================================================


def test_fresh_session_is_reused_without_logging_in():
    """The whole reason logins stay rare. Verified by the fact that it does NOT
    raise LoginContractUnverified: reaching the login path at all would."""
    authenticator = EffiAuthenticator()
    future = datetime.now(UTC) + timedelta(hours=6)

    session, did_login = authenticator.ensure_session(
        CREDENTIAL, existing_token="effi_session=abc", existing_expires_at=future
    )

    assert did_login is False
    assert session.token == "effi_session=abc"


def test_stale_session_falls_through_to_a_login():
    """And, right now, that login correctly refuses because it is unverified."""
    authenticator = EffiAuthenticator()
    past = datetime.now(UTC) - timedelta(minutes=1)

    with pytest.raises(LoginContractUnverified):
        authenticator.ensure_session(
            CREDENTIAL, existing_token="effi_session=viejo", existing_expires_at=past
        )


def test_session_never_renders_its_token():
    session = EffiSession(
        token="effi_session=secreto-de-verdad",
        expires_at=datetime.now(UTC),
        obtained_at=datetime.now(UTC),
    )
    assert "secreto-de-verdad" not in repr(session)
    assert "secreto-de-verdad" not in str(session)


# =============================================================================
# The preflight: a 403 becomes an instruction
# =============================================================================


class _FakeFetcher:
    """Answers each probe from a dict, and records what was actually asked."""

    def __init__(self, answers: dict[str, str], default: str = "granted"):
        self.answers = answers
        self.default = default
        self.asked: list[str] = []

    def probe(self, url: str, params: dict[str, str]) -> str:
        self.asked.append(url)
        for fragment, answer in self.answers.items():
            if fragment in url:
                if answer == "__expired__":
                    raise SessionExpiredError("sesión vencida")
                return answer
        return self.default


BASE = "https://effi.com.co"


def test_everything_granted_is_usable_and_says_so_plainly():
    report = run_preflight(_FakeFetcher({}), base_url=BASE)

    assert report.is_usable is True
    assert report.credential_status() == "ok"
    assert not report.missing_required


def test_a_missing_required_permission_names_it_and_says_what_breaks():
    """The message is the product. "denied" alone is a worse version of the 403."""
    fetcher = _FakeFetcher({"/reportes/novedades/": "denied"})
    report = run_preflight(fetcher, base_url=BASE)

    assert report.is_usable is False
    assert report.credential_status() == "insufficient_permissions"
    assert [r.code for r in report.missing_required] == ["novedades_guias"]

    summary = report.summary()
    assert "Novedades de guías" in summary
    assert "Effi" in summary


def test_a_missing_optional_permission_does_not_break_the_connection():
    """Refusing the whole connection over an optional permission would cost the
    merchant every metric to protect one card that says "falta un dato"."""
    fetcher = _FakeFetcher({"/reportes/articulos/": "denied"})
    report = run_preflight(fetcher, base_url=BASE)

    assert report.is_usable is True
    assert report.credential_status() == "ok"
    assert [r.code for r in report.missing_optional] == ["articulos"]
    assert "funciona" in report.summary()


def test_probing_stops_after_a_required_permission_is_denied():
    """No reason to make nine more requests to a panel that already said no."""
    fetcher = _FakeFetcher({"/app/guia_transporte/excel": "denied"})
    report = run_preflight(fetcher, base_url=BASE)

    assert len(fetcher.asked) == 1
    skipped = [r for r in report.results if r.code != "guias_transporte"]
    assert all(r.status == "unknown" for r in skipped)


def test_an_expired_session_is_not_reported_as_missing_permissions():
    """The distinction that keeps a merchant from fixing the wrong thing.

    If the session dies mid-preflight, every remaining probe would answer
    `denied` - and the merchant would go add permissions they already had.
    """
    fetcher = _FakeFetcher({"/app/guia_transporte/excel": "__expired__"})
    report = run_preflight(fetcher, base_url=BASE)

    assert report.session_valid is False
    assert report.credential_status() == "expired"
    assert "usuario y contraseña" in report.summary()


def test_a_required_permission_we_could_not_check_is_not_reported_as_working():
    """"No pudimos comprobarlo" and "funciona" are different sentences.

    Effi timing out on the guides report is not evidence that the permission is
    there. Reporting `ok` would put a green tick on a connection that has proven
    nothing, and the merchant would find out at the first failed sync instead of
    now, while they are looking at the screen.
    """
    fetcher = _FakeFetcher({"/app/guia_transporte/excel": "unreachable"})
    report = run_preflight(fetcher, base_url=BASE)

    assert report.is_usable is False
    assert report.credential_status() == "insufficient_permissions"
    assert [r.code for r in report.unproven_required] == ["guias_transporte"]
    # And it must NOT send them to fix a permission that is probably fine.
    assert "No se pudo comprobar" in report.summary()
    assert "Agrégalo" not in report.summary()


def test_permissions_without_an_endpoint_report_unknown_not_granted():
    """We do not claim to have verified something we never tested."""
    report = run_preflight(_FakeFetcher({}), base_url=BASE)
    untestable = {p.code for p in PERMISSION_PROBES if not p.path}

    for result in report.results:
        if result.code in untestable:
            assert result.status == "unknown"


# =============================================================================
# Changing the password in Effi, and what has to happen here
# =============================================================================


def test_storing_a_new_password_revives_a_connection_the_worker_gave_up_on():
    """The update path has to clear `error`/`disabled`, or the fix does nothing.

    The sequence this defends: merchant changes their Effi password → next sync
    fails → worker writes `status = 'error'` → merchant types the new password →
    ... and the worker's `status = 'active'` filter still skips the row forever.
    They fixed it, the screen said "guardada", and nothing ever synced again.

    Asserted against the SQL because that is where the CASE lives; there is no
    database in this test environment to run it against.
    """
    from pathlib import Path

    source = Path("api/routers/config.py").read_text(encoding="utf-8")
    put_body = source.split("def put_connection_credential", 1)[1]
    put_body = put_body.split("\n@router", 1)[0]

    assert "status             = CASE WHEN status IN ('error', 'disabled')" in put_body, (
        "PUT /credential dejó de reactivar la conexión. Sin eso, cambiar la "
        "contraseña la guarda pero el worker nunca vuelve a tomarla."
    )
    assert "last_error         = NULL" in put_body


def test_a_dead_credential_notifies_the_merchant():
    """A stopped sync looks exactly like a slow week. It has to announce itself.

    This is the failure mode that matters most in the whole feature: the numbers
    on screen stay plausible while the data underneath stops arriving, and a
    merchant keeps making decisions from a dashboard that froze a fortnight ago.
    """
    from pathlib import Path

    source = Path("worker/jobs.py").read_text(encoding="utf-8")
    body = source.split("def _mark_credential_status", 1)[1].split("\ndef ", 1)[0]

    assert "persist_findings" in body, (
        "El worker dejó de avisar cuando una credencial muere. El comerciante no "
        "se enteraría: el tablero se ve normal y simplemente deja de actualizarse."
    )
    assert '"severity": "critical"' in body
    # And it must survive a notification that cannot be written: stopping the
    # retry loop matters more than telling anybody about it.
    assert "except Exception:" in body


def test_we_only_ever_ask_for_read_permissions():
    """The contract with the merchant, asserted in code.

    Other tools in this market ask for Crear, Modificar and Anular because they
    also place orders. This one reads a report. If a future change starts asking
    for a write permission, it fails here first.
    """
    import re
    from pathlib import Path

    migration = Path("migrations/051_connection_vault.sql").read_text(encoding="utf-8")
    asked = set(re.findall(r"ARRAY\[([^\]]*)\]", migration))
    forbidden = ("crear", "modificar", "anular", "eliminar")

    for group in asked:
        lowered = group.lower()
        for word in forbidden:
            assert f"'{word}'" not in lowered, (
                f"La migración 051 pide el permiso de escritura «{word}». "
                "Master Data solo lee."
            )
