"""Logging in to Effi on the merchant's behalf, and knowing when to stop.

WHAT CHANGED AND WHY
--------------------
`session_fetcher.py` was written for an operator who pastes a live cookie into a
server environment variable. That works for one company and collapses for ten:
somebody with SSH has to be awake every time a session expires. This module is
what lets a merchant connect their own Effi account from the settings screen and
stay connected - the system logs in, keeps the session, and renews it.

THE LINE THIS DOES NOT CROSS
----------------------------
Automating a login is a bigger step than replaying a cookie, so the limits are
tighter, not looser:

1. ONE LOGIN PER SESSION, NOT PER REQUEST. A session is reused until it is
   actually stale (pipeline/vault.py::session_is_fresh). Hammering a login form
   is what abuse looks like from the other side, regardless of intent.

2. WRONG PASSWORD IS FINAL. A 401 on login means `invalid`, and the connection
   STOPS. No second attempt, no "maybe it was a fluke". Retrying a bad password
   is how you lock a merchant out of their own Effi account, and the person who
   pays for that is the customer, not us.

3. LOCKOUT IS A DEAD END BY DESIGN. If Effi says the account is blocked, the
   connection goes to `locked` and nothing in this codebase will try again until
   a human clears it. There is no backoff-and-retry path out of `locked` on
   purpose: a slow retry loop against a locked account is still a retry loop.

4. NO DISGUISE. Same honest User-Agent as the fetcher, same minimum interval, no
   proxy rotation, no browser fingerprint spoofing. If Effi blocks this traffic,
   the correct response is to stop and tell the merchant to go back to uploading
   the file by hand - which produces the identical dashboard.

5. THE PASSWORD IS A LOCAL VARIABLE. It arrives as a `Credential`, is used to
   build one request body, and is never logged, stored, echoed, or attached to
   an exception.

STATUS OF THE EFFI-SPECIFIC PARTS
---------------------------------
Everything above is settled. What is NOT yet verified against the real panel is
the shape of Effi's login: the exact path, the field names, whether there is a
CSRF token, whether a session arrives as a cookie or a bearer token, and how a
wrong password is signalled. Those live in `EffiLoginContract` below, isolated
in one small class with environment overrides, so confirming them against a real
account is a matter of correcting a handful of strings - not rewriting the
policy that surrounds them.

Until it is verified, `LOGIN_CONTRACT_VERIFIED` is False and `login()` refuses
to run, so nobody ships a guess into production and discovers it by locking a
customer's account. See docs/tier3-politica.md.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pipeline.vault import Credential, session_is_fresh

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://app.effi.com.co"
DEFAULT_TIMEOUT_SECONDS = 30.0
MIN_SECONDS_BETWEEN_LOGINS = 5.0
USER_AGENT = "MasterData-Analytics/1.0 (+operador autorizado por el comerciante)"

# Effi does not publish how long a session lasts. Twelve hours is a deliberately
# conservative guess: too short only costs an extra login, too long costs a
# failed sync the merchant has to look at.
ASSUMED_SESSION_LIFETIME = timedelta(hours=12)

# Flip to True only after the contract below has been confirmed against a real
# Effi account, with the confirmation written into docs/plataformas-effi-dropi.md.
# It is a module constant rather than a config flag because "someone set an env
# var by accident" must not be a way to start guessing at a login form.
LOGIN_CONTRACT_VERIFIED = False


class LoginContractUnverified(RuntimeError):
    """Someone tried to log in to Effi before the login shape was confirmed."""


class InvalidCredentials(RuntimeError):
    """Effi rejected the username or password. Terminal: do not retry."""


class AccountLocked(RuntimeError):
    """Effi says the account is blocked. Terminal until a human intervenes."""


class LoginUnavailable(RuntimeError):
    """Effi could not be reached, or answered something we cannot interpret.

    The only error here that is safe to retry later - and even then, on the
    worker's normal schedule, never in a tight loop.
    """


@dataclass(slots=True)
class EffiSession:
    """A live session and when we expect it to die."""

    token: str
    expires_at: datetime
    obtained_at: datetime

    @property
    def is_fresh(self) -> bool:
        return session_is_fresh(self.expires_at)

    def __repr__(self) -> str:  # never let a traceback carry the session
        return f"<EffiSession expires_at={self.expires_at.isoformat()} token=***>"


@dataclass(frozen=True, slots=True)
class EffiLoginContract:
    """Everything about Effi's login that we believe but have not proven.

    Every field is overridable by an environment variable so that a change on
    Effi's side - or a correction after inspecting the real panel - is a config
    edit and a worker restart, not a code deploy. That matters because this is
    the part most likely to be wrong on the first try.
    """

    # These are the NAMES of form fields and response keys, not values. "clave"
    # is what Effi's login input is called, and it is why the two `noqa: S105`
    # below are correct rather than convenient: there is no secret in this class,
    # which is precisely why it can be a frozen module-level default.
    path: str = "/login"
    username_field: str = "usuario"
    password_field: str = "clave"  # noqa: S105 - the input's name attribute
    # Where the session comes back. 'cookie' means a Set-Cookie header we replay;
    # 'json' means a token in the response body under `token_json_key`.
    session_carrier: str = "cookie"
    session_cookie_name: str = "effi_session"
    token_json_key: str = "token"  # noqa: S105 - the JSON key, not a token
    # Some panels mint a CSRF token on the login page and reject a POST without
    # it. Empty means "not needed"; if Effi does need one, name the input here.
    csrf_field: str = ""

    @classmethod
    def from_env(cls) -> EffiLoginContract:
        def pick(name: str, default: str) -> str:
            return os.environ.get(name, default)

        return cls(
            path=pick("EFFI_LOGIN_PATH", cls.path),
            username_field=pick("EFFI_LOGIN_USER_FIELD", cls.username_field),
            password_field=pick("EFFI_LOGIN_PASS_FIELD", cls.password_field),
            session_carrier=pick("EFFI_SESSION_CARRIER", cls.session_carrier),
            session_cookie_name=pick("EFFI_SESSION_COOKIE", cls.session_cookie_name),
            token_json_key=pick("EFFI_TOKEN_JSON_KEY", cls.token_json_key),
            csrf_field=pick("EFFI_LOGIN_CSRF_FIELD", cls.csrf_field),
        )


class EffiAuthenticator:
    """Turns a merchant's username and password into a usable Effi session.

    Holds no state a caller can leak: the credential is passed in per call, the
    session is returned rather than cached on the instance, and persistence is
    the caller's job (api/routers/connections.py writes it to the vault).
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        contract: EffiLoginContract | None = None,
        timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
        min_interval_seconds: float = MIN_SECONDS_BETWEEN_LOGINS,
    ) -> None:
        self._base_url = (base_url or os.environ.get("EFFI_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self._contract = contract or EffiLoginContract.from_env()
        self._timeout = timeout_seconds
        self._min_interval = min_interval_seconds
        self._last_login_at: float = 0.0

    # -- the one public operation ---------------------------------------
    def login(self, credential: Credential) -> EffiSession:
        """Exchange a username and password for a session. Never retries.

        Raises InvalidCredentials or AccountLocked for terminal outcomes, and
        LoginUnavailable for everything else. The caller maps those onto
        `connection.credential_status`; this module does not touch the database.
        """
        if not LOGIN_CONTRACT_VERIFIED:
            raise LoginContractUnverified(
                "El inicio de sesión automático en Effi todavía no está "
                "verificado contra una cuenta real. Mientras tanto, la conexión "
                "funciona subiendo el reporte a mano, que produce el mismo "
                "tablero. Ver connectors/effi/auth.py."
            )

        self._respect_rate_limit()
        response = self._post_login(credential)
        return self._session_from(response)

    def ensure_session(
        self,
        credential: Credential,
        *,
        existing_token: str | None,
        existing_expires_at: datetime | None,
    ) -> tuple[EffiSession, bool]:
        """Reuse the stored session when it is still good; log in when it is not.

        Returns `(session, did_login)` so the caller knows whether to write a new
        token to the vault. Reusing costs nothing and is the common path - a
        merchant syncing every twelve hours should log in roughly once a day, not
        once a sync.
        """
        if existing_token and session_is_fresh(existing_expires_at):
            return (
                EffiSession(
                    token=existing_token,
                    expires_at=existing_expires_at,  # type: ignore[arg-type]
                    obtained_at=datetime.now(UTC),
                ),
                False,
            )
        return self.login(credential), True

    # -- internals ------------------------------------------------------
    def _post_login(self, credential: Credential) -> Any:
        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - declared dependency
            raise LoginUnavailable("Falta la dependencia httpx") from exc

        url = f"{self._base_url}{self._contract.path}"
        form = {
            self._contract.username_field: credential.username,
            self._contract.password_field: credential.password,
        }
        headers = {
            "User-Agent": USER_AGENT,
            "Accept": "application/json, text/html",
        }

        try:
            with httpx.Client(timeout=self._timeout, follow_redirects=False) as client:
                if self._contract.csrf_field:
                    form[self._contract.csrf_field] = self._fetch_csrf(client, headers)
                response = client.post(url, data=form, headers=headers)
        except Exception as exc:
            # `form` holds the password. Never let the exception carry it: log
            # the type, discard the original with `from None`.
            raise LoginUnavailable(
                f"No se pudo contactar a Effi: {type(exc).__name__}"
            ) from None
        finally:
            # Overwrite before the dict is garbage - cheap, and it shortens the
            # window in which a heap dump would show the password.
            form[self._contract.password_field] = ""

        self._raise_for_login_status(response)
        return response

    def _fetch_csrf(self, client: Any, headers: dict[str, str]) -> str:
        """Read the CSRF token off the login page, when Effi requires one."""
        page = client.get(f"{self._base_url}{self._contract.path}", headers=headers)
        if page.status_code >= 400:
            raise LoginUnavailable(
                f"Effi no entregó el formulario de login (HTTP {page.status_code})"
            )
        token = _extract_input_value(page.text, self._contract.csrf_field)
        if not token:
            raise LoginUnavailable(
                "Effi pide un token de seguridad en el login y no se encontró en "
                "la página. La conexión automática no puede continuar."
            )
        return token

    def _raise_for_login_status(self, response: Any) -> None:
        status = response.status_code

        if status in (401, 403):
            raise InvalidCredentials(
                "Effi rechazó el usuario o la contraseña. Verifícalos entrando a "
                "Effi manualmente; no se intentará de nuevo para no bloquear la "
                "cuenta."
            )
        if status == 423:  # RFC 4918 Locked - the unambiguous case
            raise AccountLocked(
                "Effi bloqueó esta cuenta. Entra a Effi y desbloquéala antes de "
                "reconectar."
            )
        if status == 429:
            raise LoginUnavailable(
                "Effi pidió reducir el ritmo. Se reintentará en la siguiente "
                "sincronización programada."
            )
        if status >= 400:
            raise LoginUnavailable(f"Effi respondió HTTP {status} al iniciar sesión")

        # A 200 that renders the login form again is a failed login wearing a
        # success code - a very common pattern in server-rendered panels.
        body = getattr(response, "text", "") or ""
        lowered = body.lower()
        if any(marker in lowered for marker in _LOCKOUT_MARKERS):
            raise AccountLocked(
                "Effi indicó que la cuenta está bloqueada o suspendida. No se "
                "reintentará."
            )
        if any(marker in lowered for marker in _REJECTION_MARKERS):
            raise InvalidCredentials(
                "Effi devolvió la pantalla de login: el usuario o la contraseña "
                "no son correctos."
            )

    def _session_from(self, response: Any) -> EffiSession:
        now = datetime.now(UTC)
        expires_at = now + ASSUMED_SESSION_LIFETIME

        if self._contract.session_carrier == "json":
            try:
                payload = response.json()
            except Exception:
                raise LoginUnavailable(
                    "Effi respondió algo que no es JSON donde se esperaba el token"
                ) from None
            token = str(payload.get(self._contract.token_json_key) or "")
            if not token:
                raise LoginUnavailable(
                    "Effi aceptó el login pero no devolvió una sesión utilizable"
                )
            return EffiSession(token=token, expires_at=expires_at, obtained_at=now)

        # Cookie carrier: rebuild the Cookie header the fetcher will replay.
        cookies = getattr(response, "cookies", None)
        value = cookies.get(self._contract.session_cookie_name) if cookies else None
        if not value:
            raise LoginUnavailable(
                "Effi aceptó el login pero no entregó la cookie de sesión "
                f"'{self._contract.session_cookie_name}'."
            )
        return EffiSession(
            token=f"{self._contract.session_cookie_name}={value}",
            expires_at=expires_at,
            obtained_at=now,
        )

    def _respect_rate_limit(self) -> None:
        elapsed = time.monotonic() - self._last_login_at
        if self._last_login_at and elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_login_at = time.monotonic()

    def __repr__(self) -> str:  # pragma: no cover - defensive
        return f"<EffiAuthenticator base_url={self._base_url!r}>"


# Phrases a Spanish-language panel uses when a login fails but the HTTP status
# does not say so. Deliberately narrow: a false positive here would tell a
# merchant their password is wrong when it is not, and they would change a
# password that was fine.
_REJECTION_MARKERS = (
    "usuario o contrase",       # "usuario o contraseña incorrectos"
    "credenciales inv",         # "credenciales inválidas"
    "credenciales incorrect",
    "datos de acceso incorrect",
)

_LOCKOUT_MARKERS = (
    "cuenta bloquead",
    "usuario bloquead",
    "cuenta suspendid",
    "demasiados intentos",
)


def _extract_input_value(html: str, field_name: str) -> str:
    """Pull one hidden input's value out of a login form.

    A deliberately small regex instead of a parser dependency: it reads one
    attribute off one input, and if the markup is anything other than what it
    expects it returns empty and the caller fails loudly. It is not, and must
    not become, an HTML scraper.
    """
    import re

    pattern = (
        rf'<input[^>]*name=["\']{re.escape(field_name)}["\'][^>]*'
        rf'value=["\']([^"\']*)["\']'
    )
    match = re.search(pattern, html, re.IGNORECASE)
    if match:
        return match.group(1)

    # Same input with the attributes the other way round.
    pattern_reversed = (
        rf'<input[^>]*value=["\']([^"\']*)["\'][^>]*'
        rf'name=["\']{re.escape(field_name)}["\']'
    )
    match = re.search(pattern_reversed, html, re.IGNORECASE)
    return match.group(1) if match else ""
