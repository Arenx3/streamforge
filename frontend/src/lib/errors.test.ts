import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import { errorMessage, fieldErrors, translate } from "./errors";

describe("translate", () => {
  it.each([
    ["No active account found with the given credentials", "Correo o contraseña incorrectos."],
    ["This password is too common.", "Esa contraseña es muy común. Elige otra."],
    ["Enter a valid email address.", "Escribe un correo válido."],
    ["This link is invalid or has expired.", "Este enlace no es válido o ya caducó."],
  ])("%s", (message, expected) => {
    expect(translate(message)).toBe(expected);
  });

  it("never shows unknown server text", () => {
    expect(translate("django.db.utils.OperationalError at /tmp/x")).toBe(
      "Algo salió mal. Inténtalo de nuevo.",
    );
  });
});

describe("fieldErrors", () => {
  it("maps each field to its first message", () => {
    const error = new ApiError(400, { email: ["Enter a valid email address.", "Other"] });

    expect(fieldErrors(error)).toEqual({ email: "Escribe un correo válido." });
  });

  it("ignores anything that is not a validation error", () => {
    expect(fieldErrors(new ApiError(500, { email: ["x"] }))).toEqual({});
    expect(fieldErrors(new Error("boom"))).toEqual({});
  });
});

describe("errorMessage", () => {
  it("explains connection failures", () => {
    expect(errorMessage(new TypeError("Failed to fetch"))).toBe(
      "No hay conexión con el servidor.",
    );
  });

  it("explains rate limits", () => {
    expect(errorMessage(new ApiError(429, { detail: "Throttled" }))).toMatch(/Demasiados/);
  });

  it("hides server errors", () => {
    expect(errorMessage(new ApiError(500, { detail: "Traceback" }))).toBe(
      "Algo salió mal. Inténtalo de nuevo.",
    );
  });

  it("translates the detail", () => {
    expect(
      errorMessage(new ApiError(401, { detail: "No active account found with the given credentials" })),
    ).toBe("Correo o contraseña incorrectos.");
  });
});
