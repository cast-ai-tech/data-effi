"""CORS for the Effi browser extension, on ONE endpoint and nowhere else.

The global CORSMiddleware lists the web app's origins, and it must stay that
way: adding `chrome-extension://*` there would let any installed extension read
every authenticated response the API gives. The extension needs exactly one
route - `POST /config/effi/pairing/redeem` - which takes no cookies and no JWT
(the one-time code in the body is the credential), so that is the only path
where an extension origin is answered.

In practice a Manifest V3 popup with the API in its `host_permissions` is not
subject to CORS at all. This exists for the cases where it is (a stricter
browser build, a future Chrome change, the extension calling from a content
script) and so the rule is written down instead of depending on that exemption.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response

from api.settings import Settings

# Chrome and Opera (Chromium) both use this scheme; ids are 32 chars a-p.
_EXTENSION_ORIGIN = re.compile(r"^chrome-extension://[a-p]{32}$")


def extension_origin_allowed(origin: str | None, settings: Settings) -> bool:
    if not origin or not _EXTENSION_ORIGIN.match(origin):
        return False
    allowlist = settings.effi_extension_origin_list
    return not allowlist or origin in allowlist


def build_middleware(
    settings: Settings, path: str
) -> Callable[[Request, Callable[[Request], Awaitable[Response]]], Awaitable[Response]]:
    async def effi_extension_cors(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if request.url.path != path:
            return await call_next(request)

        origin = request.headers.get("origin")
        allowed = extension_origin_allowed(origin, settings)

        if request.method == "OPTIONS" and request.headers.get(
            "access-control-request-method"
        ):
            if not allowed:
                return Response(status_code=403)
            return Response(
                status_code=204,
                headers={
                    "Access-Control-Allow-Origin": origin or "",
                    "Access-Control-Allow-Methods": "POST",
                    "Access-Control-Allow-Headers": "Content-Type",
                    "Access-Control-Max-Age": "600",
                    "Vary": "Origin",
                },
            )

        response = await call_next(request)
        if allowed and origin:
            # No Allow-Credentials: the endpoint never reads a cookie of ours.
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        return response

    return effi_extension_cors
