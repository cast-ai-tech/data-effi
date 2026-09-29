import { describe, expect, it } from "vitest";

import { ApiError, withInvalidFields } from "@/lib/api";

const invalid = (fields: { field: string; reason: string }[]) => ({
  error: {
    code: "validation_error",
    message: "Revisa los datos enviados: hay campos inválidos.",
    detail: { fields },
  },
});

describe("validation errors name the field to fix", () => {
  it("names a single known field", () => {
    const error = new ApiError(422, invalid([{ field: "email", reason: "special-use" }]), "x");
    expect(error.message).toBe("Hay datos que no son válidos. Revisa el correo.");
  });

  it("lists several fields once each", () => {
    expect(
      withInvalidFields(
        invalid([
          { field: "email", reason: "a" },
          { field: "password", reason: "b" },
          { field: "password", reason: "c" },
          { field: "full_name", reason: "d" },
        ]),
      ),
    ).toBe("Hay datos que no son válidos. Revisa el correo, la contraseña y el nombre.");
  });

  it("keeps the API message when no field is known", () => {
    const error = new ApiError(422, invalid([{ field: "mystery", reason: "?" }]), "x");
    expect(error.message).toBe("Revisa los datos enviados: hay campos inválidos.");
  });

  it("leaves every other error alone", () => {
    const body = { error: { code: "conflict", message: "Ya existe", detail: {} } };
    expect(new ApiError(409, body, "x").message).toBe("Ya existe");
    expect(new ApiError(500, null, "Sin conexión").message).toBe("Sin conexión");
  });
});
