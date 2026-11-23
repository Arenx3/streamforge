import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { endSession, startSession } from "../../lib/api";
import type * as PlayerModule from "../player/VideoPlayer";
import { qualityOptions } from "../player/VideoPlayer";
import { useShowsPlayer, type VideoDetail } from "./api";
import { VideoPage } from "./VideoPage";

vi.mock("../player/VideoPlayer", async (importOriginal) => ({
  ...(await importOriginal<typeof PlayerModule>()),
  VideoPlayer: ({ url }: { url: string }) => <div data-testid="player">{url}</div>,
}));

const access = `h.${btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 600 }))}.s`;
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const detail = (overrides: Partial<VideoDetail>): VideoDetail => ({
  id: "v1",
  title: "Demo de producto",
  description: "Una descripción",
  status: "READY",
  progress: 100,
  thumbnail_url: null,
  duration_seconds: 65,
  width: 1920,
  height: 1080,
  size_bytes: 1024,
  created_at: "2026-11-22T12:00:00Z",
  updated_at: "2026-11-22T12:00:00Z",
  error_message: "",
  processed_at: null,
  playback_url: "http://localhost/api/playback/token/master.m3u8",
  ...overrides,
});

function Rail() {
  return <output data-testid="rail">{useShowsPlayer() ? "compact" : "full"}</output>;
}

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/videos/v1"]}>
        <Rail />
        <Routes>
          <Route path="/videos/:id" element={<VideoPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("VideoPage", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    startSession({ access, refresh: "r1" });
  });

  afterEach(() => {
    endSession();
    vi.unstubAllGlobals();
  });

  it("plays a ready video", async () => {
    fetchMock.mockResolvedValue(json(200, detail({})));
    renderPage();

    expect(await screen.findByTestId("player")).toHaveTextContent("master.m3u8");
    expect(screen.getByRole("heading", { name: "Demo de producto" })).toBeInTheDocument();
    expect(screen.getByText("Una descripción")).toBeInTheDocument();
  });

  it("shows real progress while processing", async () => {
    fetchMock.mockResolvedValue(
      json(200, detail({ status: "PROCESSING", progress: 47, playback_url: null })),
    );
    renderPage();

    expect(await screen.findByText("47 %")).toBeInTheDocument();
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuetext", "Proceso: 47 %");
    expect(screen.queryByTestId("player")).not.toBeInTheDocument();
  });

  it("explains a failure in Spanish", async () => {
    fetchMock.mockResolvedValue(
      json(
        200,
        detail({
          status: "FAILED",
          progress: 5,
          playback_url: null,
          error_message: "The file is not a supported video.",
        }),
      ),
    );
    renderPage();

    expect(await screen.findByText("El archivo no es un video compatible.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Subir de nuevo" })).toHaveAttribute("href", "/upload");
  });

  it("handles a video that does not exist", async () => {
    fetchMock.mockResolvedValue(json(404, { detail: "Not found." }));
    renderPage();

    expect(await screen.findByRole("heading", { name: "Este video no existe" })).toBeInTheDocument();
    expect(screen.getByTestId("rail")).toHaveTextContent("full");
  });

  it.each([
    ["READY", "compact"],
    ["PROCESSING", "full"],
    ["FAILED", "full"],
  ] as const)("sizes the rail for a %s video", async (status, rail) => {
    const playback_url = status === "READY" ? detail({}).playback_url : null;
    fetchMock.mockResolvedValue(json(200, detail({ status, playback_url })));
    renderPage();

    await screen.findByRole("heading", { name: "Demo de producto" });
    expect(screen.getByTestId("rail")).toHaveTextContent(rail);
  });
});

describe("qualityOptions", () => {
  const levels = [
    { index: 0, height: 360 },
    { index: 1, height: 720 },
    { index: 2, height: 1080 },
  ];

  it("lists auto first, then from highest to lowest", () => {
    expect(qualityOptions(levels, -1, -1).map(({ label }) => label)).toEqual([
      "Auto",
      "1080p",
      "720p",
      "360p",
    ]);
  });

  it("shows what auto is playing", () => {
    expect(qualityOptions(levels, 1, -1)[0]!.label).toBe("Auto (720p)");
  });

  it("keeps auto plain when a quality is forced", () => {
    expect(qualityOptions(levels, 2, 2)[0]!.label).toBe("Auto");
  });
});
