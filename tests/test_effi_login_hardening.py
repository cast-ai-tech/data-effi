"""Effi login: the failure shapes a real server-rendered panel produces.

Effi is CodeIgniter (tools/effi-capture/README.md): a form at /ingreso, a POST to
/ingreso/validar_usuario, a `ci_session` cookie minted on the GET and a CSRF
`token` tied to it, and a reCAPTCHA on the button. Each test here is one way the
first real login would otherwise have gone wrong - quietly.

No network: httpx.MockTransport plays Effi.
"""

from __future__ import annotations

import httpx
import pytest

from connectors.effi import auth
from connectors.effi.auth import (
    CaptchaRequired,
    EffiAuthenticator,
    EffiLoginContract,
    InvalidCredentials,
    LoginContractUnverified,
)
from pipeline.vault import Credential

CREDENTIAL = Credential(username="reportes@tienda.co", password="clave-de-prueba")

EFFI_CONTRACT = EffiLoginContract(
    path="/ingreso/validar_usuario",
    page_path="/ingreso",
    username_field="email",
    password_field="password",
    session_carrier="cookie",
    session_cookie_name="ci_session",
    csrf_field="token",
)

LOGIN_PAGE = (
    '<form action="/ingreso/validar_usuario" method="post">'
    '<input type="hidden" name="token" value="csrf-123">'
    '<input name="email"><input type="password" name="password">'
    "</form>"
)


class _Resp:
    def __init__(self, status_code=200, text="", headers=None):
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}
        self.cookies = {}


@pytest.fixture
def verified(monkeypatch):
    """Pretend the contract was verified, only for the test that needs it."""
    monkeypatch.setattr(auth, "LOGIN_CONTRACT_VERIFIED", True)


@pytest.fixture
def effi(monkeypatch):
    """Route every httpx.Client the authenticator opens to a scripted Effi."""
    calls: list[httpx.Request] = []
    script: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        route = script[f"{request.method} {request.url.path}"]
        return route(request) if callable(route) else route

    real_client = httpx.Client

    def fake_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", fake_client)
    return script, calls


def _authenticator() -> EffiAuthenticator:
    return EffiAuthenticator(
        base_url="https://effi.test", contract=EFFI_CONTRACT, min_interval_seconds=0
    )


# -- a redirect back to the form is a rejection, not a login -----------------


def test_a_redirect_back_to_the_login_page_is_a_rejection():
    """CodeIgniter answers a wrong password with 303 → /ingreso, not a 401.

    Read as success, that redirect stored an anonymous session as a login, the
    next probe bounced, and the worker logged in again - with the same wrong
    password - on every run.
    """
    response = _Resp(303, headers={"location": "https://effi.test/ingreso"})
    with pytest.raises(InvalidCredentials):
        _authenticator()._raise_for_login_status(response)


def test_a_redirect_into_the_panel_is_a_success():
    response = _Resp(303, headers={"location": "https://effi.test/app/inicio"})
    _authenticator()._raise_for_login_status(response)  # no raise


# -- the captcha stops us BEFORE the password is sent ------------------------


def test_a_captcha_on_the_form_stops_before_posting_the_password(effi, verified):
    script, calls = effi
    script["GET /ingreso"] = httpx.Response(
        200, text=LOGIN_PAGE + '<button class="g-recaptcha" data-sitekey="x">'
    )

    with pytest.raises(CaptchaRequired) as exc:
        _authenticator().login(CREDENTIAL)

    # Same family as "contract unverified": callers must not blame the merchant.
    assert isinstance(exc.value, LoginContractUnverified)
    assert [c.method for c in calls] == ["GET"], "se envió la contraseña igual"


def test_the_csrf_token_is_read_from_the_page_that_renders_the_form(effi, verified):
    """Not from the POST endpoint, which renders nothing."""
    script, calls = effi
    script["GET /ingreso"] = httpx.Response(200, text=LOGIN_PAGE)
    script["POST /ingreso/validar_usuario"] = httpx.Response(
        303,
        headers={
            "location": "/app/inicio",
            "set-cookie": "ci_session=autenticada; Path=/",
        },
    )

    session = _authenticator().login(CREDENTIAL)

    assert session.token == "ci_session=autenticada"
    body = calls[1].content.decode()
    assert "token=csrf-123" in body


def test_a_session_cookie_minted_on_the_form_page_is_still_found(effi, verified):
    """CodeIgniter sets ci_session on the GET and may not re-send it on the POST."""
    script, _ = effi
    script["GET /ingreso"] = httpx.Response(
        200, text=LOGIN_PAGE, headers={"set-cookie": "ci_session=desde-el-get; Path=/"}
    )
    script["POST /ingreso/validar_usuario"] = httpx.Response(
        303, headers={"location": "/app/inicio"}
    )

    session = _authenticator().login(CREDENTIAL)

    assert session.token == "ci_session=desde-el-get"


def test_errors_raised_while_reading_the_form_keep_their_message(effi, verified):
    """They used to be flattened into "No se pudo contactar a Effi: LoginUnavailable"."""
    script, _ = effi
    script["GET /ingreso"] = httpx.Response(200, text="<form></form>")

    with pytest.raises(auth.LoginUnavailable) as exc:
        _authenticator().login(CREDENTIAL)

    assert "token de seguridad" in str(exc.value)


# -- empty env vars mean "use the default" -----------------------------------


def test_empty_environment_variables_do_not_blank_the_contract(monkeypatch):
    """.env.example and render.yaml declare every EFFI_* variable EMPTY.

    With `os.environ.get(name, default)` that empty string won: the login path
    became '' and the cookie name ''. Empty must mean "not configured".
    """
    for name in (
        "EFFI_LOGIN_PATH", "EFFI_LOGIN_PAGE_PATH", "EFFI_LOGIN_USER_FIELD",
        "EFFI_LOGIN_PASS_FIELD", "EFFI_SESSION_CARRIER", "EFFI_SESSION_COOKIE",
        "EFFI_TOKEN_JSON_KEY", "EFFI_LOGIN_CSRF_FIELD",
    ):
        monkeypatch.setenv(name, "")

    contract = EffiLoginContract.from_env()

    assert contract == EffiLoginContract()


def test_unset_environment_variables_fall_back_to_real_strings(monkeypatch):
    """With slots=True, `cls.path` is a slot descriptor, not "/login".

    from_env() used to return "<member 'path' of 'EffiLoginContract' objects>"
    as the login path for every variable nobody had set.
    """
    for name in ("EFFI_LOGIN_PATH", "EFFI_SESSION_COOKIE", "EFFI_LOGIN_CSRF_FIELD"):
        monkeypatch.delenv(name, raising=False)

    contract = EffiLoginContract.from_env()

    assert contract.path == "/login"
    assert contract.session_cookie_name == "effi_session"
    assert contract.csrf_field == ""
