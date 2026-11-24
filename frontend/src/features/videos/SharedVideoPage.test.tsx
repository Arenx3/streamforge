import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { endSession, startSession } from "../../lib/api";
import type { SharedVideo } from "./api";
import { SharedVideoPage } from "./SharedVideoPage";

const player = vi.hoisted(() => ({ getFreshUrl: null as null | (() => Promise<string | null>) }));

vi.mock("../player/VideoPlayer", () => ({
  VideoPlayer: ({ url, getFreshUrl }: { url: string; getFreshUrl: () => Promise<string | null> }) => {
    player.getFreshUrl = getFreshUrl;
    return <div data-testid="player">{url}</div>;
  },
}));

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const shared: SharedVideo = {
  title: "Función de estreno",
  description: "Para todo el público",
  duration_seconds: 65,
  width: 1920,
  height: 1080,
  thumbnail_url: null,
  playback_url: "http://localhost/api/public/playback/signed/master.m3u8",
};

function renderPage() {
  const client = new QueryClient();
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/v/tok"]}>
        <Routes>
          <Route path="/v/:token" element={<SharedVideoPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("SharedVideoPage", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    endSession();
    vi.unstubAllGlobals();
  });

  it("plays the video for a visitor", async () => {
    fetchMock.mockImplementation(async () => json(200, shared));
    renderPage();

    expect(await screen.findByTestId("player")).toHaveTextContent("master.m3u8");
    expect(screen.getByRole("heading", { name: "Función de estreno" })).toBeInTheDocument();
    expect(screen.getByText("Para todo el público")).toBeInTheDocument();
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/public/videos/tok/");
  });

  it("never sends the session of whoever opens it", async () => {
    startSession({ access: "h.e30.s" });
    fetchMock.mockImplementation(async () => json(200, shared));
    renderPage();

    await screen.findByTestId("player");
    const headers = fetchMock.mock.calls[0]?.[1]?.headers as Record<string, string>;
    expect(headers.Authorization).toBeUndefined();
  });

  it("shows the title in the browser tab", async () => {
    fetchMock.mockImplementation(async () => json(200, shared));
    renderPage();

    await screen.findByTestId("player");
    expect(document.title).toBe("Función de estreno · StreamForge");
  });

  it("says when the link no longer works", async () => {
    fetchMock.mockImplementation(async () => json(404, { detail: "Not found." }));
    renderPage();

    expect(await screen.findByRole("heading", { name: "Función cancelada" })).toBeInTheDocument();
    expect(screen.queryByTestId("player")).not.toBeInTheDocument();
  });

  it("replaces the player when the link is revoked while watching", async () => {
    fetchMock.mockImplementationOnce(async () => json(200, shared));
    fetchMock.mockImplementation(async () => json(404, { detail: "Not found." }));
    renderPage();
    await screen.findByTestId("player");

    await expect(player.getFreshUrl?.()).resolves.toBeNull();

    expect(await screen.findByRole("heading", { name: "Función cancelada" })).toBeInTheDocument();
    expect(screen.queryByTestId("player")).not.toBeInTheDocument();
  });

  it("offers a retry when the server fails", async () => {
    fetchMock.mockImplementationOnce(async () => json(503, { detail: "Storage is temporarily unavailable." }));
    fetchMock.mockImplementation(async () => json(200, shared));
    renderPage();

    fireEvent.click(await screen.findByRole("button", { name: "Reintentar" }));

    await waitFor(() => expect(screen.getByTestId("player")).toBeInTheDocument());
  });
});
