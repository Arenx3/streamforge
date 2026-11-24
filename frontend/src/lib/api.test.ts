import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { api, ApiError, endSession, hasSession, onSessionEnd, startSession } from "./api";

const jwt = (secondsFromNow: number) =>
  `h.${btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + secondsFromNow }))}.s`;
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

describe("api", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    endSession();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  const authHeader = (call: number) =>
    (fetchMock.mock.calls[call]![1]!.headers as Record<string, string>).Authorization;

  it("sends the access token", async () => {
    const token = jwt(600);
    startSession({ access: token });
    fetchMock.mockResolvedValueOnce(json(200, { ok: true }));

    await expect(api("/videos/")).resolves.toEqual({ ok: true });

    expect(fetchMock.mock.calls[0]![0]).toBe("/api/videos/");
    expect(authHeader(0)).toBe(`Bearer ${token}`);
  });

  it("never sends a token to public endpoints", async () => {
    startSession({ access: jwt(600) });
    fetchMock.mockResolvedValueOnce(json(200, {}));

    await api("/config/", { auth: false });

    expect(authHeader(0)).toBeUndefined();
  });

  it("renews an expiring token before the request", async () => {
    const renewed = jwt(600);
    startSession({ access: jwt(5) });
    fetchMock
      .mockResolvedValueOnce(json(200, { access: renewed }))
      .mockResolvedValueOnce(json(200, {}));

    await api("/videos/");

    expect(fetchMock.mock.calls[0]![0]).toBe("/api/auth/token/refresh/");
    // The refresh token travels in its cookie, never in the request.
    expect(fetchMock.mock.calls[0]![1]).toEqual({ method: "POST", credentials: "same-origin" });
    expect(authHeader(1)).toBe(`Bearer ${renewed}`);
  });

  it("keeps no token in storage", () => {
    localStorage.setItem("streamforge.refresh", "left by an older version");

    startSession({ access: jwt(600) });

    expect(localStorage.getItem("streamforge.session")).toBe("1");
    expect(localStorage.getItem("streamforge.refresh")).toBeNull();
    expect(JSON.stringify({ ...localStorage })).not.toContain("h.");
  });

  it("does not ask for a renewal without a session", async () => {
    fetchMock.mockResolvedValueOnce(json(401, {}));

    await expect(api("/videos/")).rejects.toBeInstanceOf(ApiError);

    expect(fetchMock).toHaveBeenCalledOnce();
  });

  it("tells other tabs when the session ends", () => {
    const ended = vi.fn();
    const stop = onSessionEnd(ended);

    window.dispatchEvent(new StorageEvent("storage", { key: "streamforge.session", newValue: null }));

    expect(ended).toHaveBeenCalledOnce();
    stop();
  });

  it("uses each refresh token once across parallel requests", async () => {
    startSession({ access: jwt(5) });
    fetchMock.mockImplementation(async (input) =>
      input === "/api/auth/token/refresh/"
        ? json(200, { access: jwt(600) })
        : json(200, {}),
    );

    await Promise.all([api("/a/"), api("/b/"), api("/c/")]);

    const rotations = fetchMock.mock.calls.filter(([url]) => url === "/api/auth/token/refresh/");
    expect(rotations).toHaveLength(1);
  });

  it("retries once after a 401", async () => {
    const renewed = jwt(600);
    startSession({ access: jwt(600) });
    fetchMock
      .mockResolvedValueOnce(json(401, {}))
      .mockResolvedValueOnce(json(200, { access: renewed }))
      .mockResolvedValueOnce(json(200, { done: true }));

    await expect(api("/videos/")).resolves.toEqual({ done: true });
    expect(authHeader(2)).toBe(`Bearer ${renewed}`);
  });

  it("ends the session when the refresh token is rejected", async () => {
    const ended = vi.fn();
    const stop = onSessionEnd(ended);
    startSession({ access: jwt(5) });
    fetchMock
      .mockResolvedValueOnce(json(401, {}))
      .mockResolvedValueOnce(json(401, { detail: "No session" }));

    await expect(api("/videos/")).rejects.toBeInstanceOf(ApiError);

    expect(ended).toHaveBeenCalledOnce();
    expect(hasSession()).toBe(false);
    stop();
  });

  it("exposes the status and body of errors", async () => {
    startSession({ access: jwt(600) });
    fetchMock.mockResolvedValueOnce(json(400, { title: ["Required"] }));

    const error = await api("/videos/").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(400);
    expect((error as ApiError).data).toEqual({ title: ["Required"] });
  });

  it("handles empty responses", async () => {
    startSession({ access: jwt(600) });
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));

    await expect(api("/videos/1/", { method: "DELETE" })).resolves.toBeUndefined();
  });
});
