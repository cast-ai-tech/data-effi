// node --test extension/effi-connector/lib.test.mjs   (sin dependencias)
import assert from "node:assert/strict";
import { readdirSync, readFileSync } from "node:fs";
import { test } from "node:test";

import {
  buildPayload,
  describeResponse,
  normalizeCode,
  normalizeOrigin,
  pickSessionCookies,
} from "./lib.js";

test("el código se normaliza como en el servidor", () => {
  assert.equal(normalizeCode(" abcd efgh-jkmn "), "ABCD-EFGH-JKMN");
  assert.equal(normalizeCode("ABCD-EFGH-JKM1"), null); // '1' no existe
  assert.equal(normalizeCode("corto"), null);
});

test("solo salen las cookies de sesión, y la del dominio principal", () => {
  const cookies = [
    { name: "_ga", value: "GA1", domain: ".effi.com.co", path: "/" },
    { name: "ci_session", value: "sub", domain: "app.effi.com.co", path: "/" },
    { name: "ci_session", value: "principal", domain: "effi.com.co", path: "/" },
  ];
  const picked = pickSessionCookies(cookies, ["ci_session"], "effi.com.co");
  assert.deepEqual(picked.map((c) => c.value), ["principal"]);
});

test("el envío lleva nombre, valor y vencimiento; nada más", () => {
  const payload = buildPayload(
    "ABCD-EFGH-JKMN",
    [{ name: "ci_session", value: "v", domain: "effi.com.co", httpOnly: true, expirationDate: 1900000000 }],
    "UA",
  );
  assert.deepEqual(payload, {
    code: "ABCD-EFGH-JKMN",
    cookies: [{ name: "ci_session", value: "v", expires_at: 1900000000 }],
    user_agent: "UA",
  });
  const sesion = buildPayload("X", [{ name: "ci_session", value: "v", session: true }], "UA");
  assert.equal(sesion.cookies[0].expires_at, null);
});

test("las respuestas se dicen en palabras", () => {
  assert.equal(
    describeResponse(200, { connected: true, connection_name: "Effi CO", summary: "" }).tono,
    "ok",
  );
  assert.equal(
    describeResponse(200, { connected: false, credential_status: "session_expired", summary: "vencida" }).texto,
    "vencida",
  );
  assert.equal(describeResponse(404, { error: { message: "Ese código no sirve" } }).texto, "Ese código no sirve");
  assert.equal(describeResponse(500, null).tono, "error");
});

test("el servidor configurable solo acepta https (o localhost)", () => {
  assert.equal(normalizeOrigin("https://api.x.com/algo"), "https://api.x.com");
  assert.equal(normalizeOrigin("http://localhost:8000"), "http://localhost:8000");
  assert.equal(normalizeOrigin("http://api.x.com"), null);
  assert.equal(normalizeOrigin("javascript:alert(1)"), null);
});

test("la extensión habla de Master Data, no del nombre viejo", () => {
  const dir = new URL(".", import.meta.url);
  for (const name of readdirSync(dir)) {
    if (!/\.(js|json|html)$/.test(name)) continue;
    const text = readFileSync(new URL(name, dir), "utf8");
    assert.doesNotMatch(text, /Data Effi|dataeffi(?!_)/i, name);
  }
});
