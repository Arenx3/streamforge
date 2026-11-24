import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { endSession, startSession } from "../../lib/api";
import type { Video } from "./api";
import { LibraryPage } from "./LibraryPage";
import { videoJourney } from "./VideoTicket";

const access = `h.${btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 600 }))}.s`;
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

const video = (overrides: Partial<Video>): Video => ({
  id: "11111111-1111-1111-1111-111111111111",
  title: "Muestra",
  description: "",
  status: "READY",
  progress: 100,
  error_message: "",
  thumbnail_url: null,
  duration_seconds: 65,
  width: 1920,
  height: 1080,
  size_bytes: 39 * 1024 ** 2,
  created_at: "2026-11-22T00:00:00Z",
  updated_at: "2026-11-22T00:00:00Z",
  ...overrides,
});

const SUMMARY = { PENDING_UPLOAD: 0, UPLOADED: 0, PROCESSING: 1, READY: 1, FAILED: 0 };

function renderLibrary(entry = "/") {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[entry]}>
        <LibraryPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("LibraryPage", () => {
  const fetchMock = vi.fn<typeof fetch>();

  const respond = (results: Video[], summary = SUMMARY) =>
    fetchMock.mockImplementation(async (url, init) => {
      if (init?.method === "DELETE") return new Response(null, { status: 204 });
      if (String(url).includes("/summary/")) return json(200, summary);
      return json(200, { count: results.length, next: null, previous: null, results });
    });

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    startSession({ access });
  });

  afterEach(() => {
    endSession();
    vi.unstubAllGlobals();
  });

  it("lists videos with their state and real progress", async () => {
    respond([
      video({}),
      video({ id: "2", title: "Demo.mp4", status: "PROCESSING", progress: 47 }),
    ]);
    renderLibrary();

    const row = (await screen.findByText("Demo.mp4")).closest("tr")!;
    expect(within(row).getAllByText("Procesando").length).toBeGreaterThan(0);
    // One meter per layout: CSS shows the mobile or the desktop one.
    for (const meter of within(row).getAllByRole("progressbar")) {
      expect(meter).toHaveAttribute("aria-valuetext", "Proceso: 47 %");
    }
    expect(within(row).getAllByText("47 %").length).toBeGreaterThan(0);
    expect(await screen.findByText("2 videos · 1 listo · 1 en proceso")).toBeInTheDocument();
  });

  it.each([
    ["UPLOADED", 0, ""],
    ["PROCESSING", 47, ""],
    ["FAILED", 31, "The file is not a supported video."],
    ["FAILED", 0, "Upload abandoned before it was completed."],
  ] as const)("fills a %s row like its ticket at %d %%", async (status, progress, error_message) => {
    respond([video({ title: "Demo.mp4", status, progress, error_message })]);
    renderLibrary();

    const row = (await screen.findByText("Demo.mp4")).closest("tr")!;
    const journey = videoJourney({ status, progress, error_message });
    const [meter] = within(row).getAllByRole("progressbar");
    expect(meter).toHaveAttribute("aria-valuenow", String(Math.round(journey.percent)));
    expect(meter).toHaveAttribute("data-tone", journey.tone);
  });

  it("asks the API for the selected filter", async () => {
    respond([video({ status: "PROCESSING", progress: 3 })]);
    renderLibrary("/?estado=en-proceso");

    await screen.findByText("Muestra");

    const urls = fetchMock.mock.calls.map(([url]) => decodeURIComponent(String(url)));
    expect(urls.some((url) => url.includes("status=UPLOADED,PROCESSING"))).toBe(true);
  });

  it("invites to upload when the library is empty", async () => {
    respond([], { PENDING_UPLOAD: 0, UPLOADED: 0, PROCESSING: 0, READY: 0, FAILED: 0 });
    renderLibrary();

    expect(await screen.findByText("La cartelera está vacía")).toBeInTheDocument();
    expect(screen.getByText("Todavía no tienes videos.")).toBeInTheDocument();
  });

  it("explains an empty filter", async () => {
    respond([]);
    renderLibrary("/?estado=con-error");

    expect(await screen.findByText("No hay videos en “Con error”.")).toBeInTheDocument();
  });

  it("deletes only after confirming", async () => {
    respond([video({})]);
    renderLibrary();

    await userEvent.click(await screen.findByRole("button", { name: "Borrar Muestra" }));
    expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(false);

    await userEvent.click(screen.getByRole("button", { name: "Borrar video", hidden: true }));

    await waitFor(() =>
      expect(fetchMock.mock.calls.some(([, init]) => init?.method === "DELETE")).toBe(true),
    );
  });

  it("offers a retry when loading fails", async () => {
    fetchMock.mockResolvedValue(json(500, {}));
    renderLibrary();

    expect(await screen.findByRole("alert")).toHaveTextContent("Algo salió mal");
    expect(screen.getByRole("button", { name: "Reintentar" })).toBeInTheDocument();
  });
});
