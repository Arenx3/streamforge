import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { endSession, startSession } from "../../lib/api";
import { UploadQueue } from "./queue";
import { contentTypeOf, isAbort, titleFrom, uploadVideo } from "./uploader";

const access = `h.${btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 600 }))}.s`;
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

class FakeXhr {
  static requests: FakeXhr[] = [];
  static statusFor: (url: string, attempt: number) => number = () => 200;
  static manual = false;
  static attempts = new Map<string, number>();
  static peak = 0;
  static open_ = 0;

  url = "";
  body: Blob | null = null;
  status = 0;
  upload: { onprogress?: (event: { loaded: number }) => void } = {};
  onload?: () => void;
  onerror?: () => void;
  onabort?: () => void;

  open(_method: string, url: string) {
    this.url = url;
  }

  send(body: Blob) {
    this.body = body;
    FakeXhr.requests.push(this);
    FakeXhr.open_ += 1;
    FakeXhr.peak = Math.max(FakeXhr.peak, FakeXhr.open_);
    if (!FakeXhr.manual) queueMicrotask(() => setTimeout(() => this.respond(), 0));
  }

  respond() {
    const attempt = (FakeXhr.attempts.get(this.url) ?? 0) + 1;
    FakeXhr.attempts.set(this.url, attempt);
    FakeXhr.open_ -= 1;
    this.status = FakeXhr.statusFor(this.url, attempt);
    if (this.status === 0) {
      this.onerror?.();
      return;
    }
    this.upload.onprogress?.({ loaded: this.body?.size ?? 0 });
    this.onload?.();
  }

  abort() {
    FakeXhr.open_ -= 1;
    this.onabort?.();
  }
}

const SIZES = [4, 4, 4, 4, 2];
const PARTS = SIZES.map((size, index) => ({
  part_number: index + 1,
  size,
  url: `http://s3.test/part-${index + 1}`,
}));
const file = () => new File(["abcdefghijklmnopqr"], "Mi video.mp4", { type: "video/mp4" });
const callbacks = () => ({ onProgress: vi.fn(), onFinishing: vi.fn() });

describe("upload helpers", () => {
  it("falls back to the extension when the browser gives no type", () => {
    expect(contentTypeOf({ name: "clip.MKV", type: "" })).toBe("video/x-matroska");
    expect(contentTypeOf({ name: "clip.mp4", type: "video/mp4" })).toBe("video/mp4");
    expect(contentTypeOf({ name: "notes.pdf", type: "application/pdf" })).toBeNull();
  });

  it("builds the title from the file name", () => {
    expect(titleFrom("Mi video final.v2.mp4")).toBe("Mi video final.v2");
    expect(titleFrom(".mp4")).toBe(".mp4");
    expect(titleFrom(`${"x".repeat(300)}.mp4`)).toHaveLength(255);
  });
});

describe("uploadVideo", () => {
  const fetchMock = vi.fn<typeof fetch>();

  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true, advanceTimeDelta: 50 });
    FakeXhr.requests = [];
    FakeXhr.statusFor = () => 200;
    FakeXhr.manual = false;
    FakeXhr.attempts = new Map();
    FakeXhr.peak = 0;
    FakeXhr.open_ = 0;
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    vi.stubGlobal("fetch", fetchMock);
    fetchMock.mockReset();
    fetchMock.mockImplementation(async (url, init) => {
      if (url === "/api/videos/") {
        return json(201, { video: { id: "v1" }, upload: { parts: PARTS } });
      }
      if (init?.method === "DELETE") return new Response(null, { status: 204 });
      return json(200, { id: "v1", status: "UPLOADED" });
    });
    startSession({ access, refresh: "r1" });
  });

  afterEach(() => {
    endSession();
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  const called = (method: string) =>
    fetchMock.mock.calls.filter(([, init]) => init?.method === method).map(([url]) => url);

  it("sends each part with its exact slice and completes", async () => {
    const handlers = callbacks();

    await uploadVideo(file(), new AbortController().signal, handlers);

    const sizes = Object.fromEntries(FakeXhr.requests.map((r) => [r.url, r.body!.size]));
    expect(sizes).toEqual(Object.fromEntries(PARTS.map((part) => [part.url, part.size])));
    expect(called("POST")).toContain("/api/videos/v1/complete/");
    expect(handlers.onFinishing).toHaveBeenCalledOnce();
    expect(handlers.onProgress).toHaveBeenLastCalledWith(18);
  });

  it("never sends more than three parts at once", async () => {
    await uploadVideo(file(), new AbortController().signal, callbacks());

    expect(FakeXhr.peak).toBe(3);
  });

  it("describes the file to the API", async () => {
    await uploadVideo(file(), new AbortController().signal, callbacks());

    const body = JSON.parse(String(fetchMock.mock.calls[0]![1]!.body));
    expect(body).toEqual({ title: "Mi video", content_type: "video/mp4", size_bytes: 18 });
  });

  it("retries a part after a network error", async () => {
    FakeXhr.statusFor = (url, attempt) => (url.endsWith("part-2") && attempt === 1 ? 0 : 200);

    await uploadVideo(file(), new AbortController().signal, callbacks());

    expect(FakeXhr.attempts.get("http://s3.test/part-2")).toBe(2);
    expect(called("POST")).toContain("/api/videos/v1/complete/");
  });

  it("does not retry a rejected part and cleans up", async () => {
    FakeXhr.statusFor = (url) => (url.endsWith("part-1") ? 403 : 200);

    await expect(
      uploadVideo(file(), new AbortController().signal, callbacks()),
    ).rejects.toThrow();

    expect(FakeXhr.attempts.get("http://s3.test/part-1")).toBe(1);
    expect(called("DELETE")).toEqual(["/api/videos/v1/"]);
    expect(called("POST")).not.toContain("/api/videos/v1/complete/");
  });

  it("gives up after four attempts", async () => {
    FakeXhr.statusFor = (url) => (url.endsWith("part-5") ? 503 : 200);

    const running = uploadVideo(file(), new AbortController().signal, callbacks()).catch(
      (caught: unknown) => caught,
    );
    await vi.advanceTimersByTimeAsync(10_000);

    expect(await running).toBeInstanceOf(Error);
    expect(FakeXhr.attempts.get("http://s3.test/part-5")).toBe(4);
  });

  it("cancels, stops the parts and removes the upload", async () => {
    FakeXhr.manual = true;
    const controller = new AbortController();
    const running = uploadVideo(file(), controller.signal, callbacks());
    await vi.waitFor(() => expect(FakeXhr.requests).toHaveLength(3));

    controller.abort();

    const error = await running.catch((caught: unknown) => caught);
    expect(isAbort(error)).toBe(true);
    expect(FakeXhr.requests).toHaveLength(3);
    expect(called("DELETE")).toEqual(["/api/videos/v1/"]);
  });
});

describe("UploadQueue", () => {
  const video = (name: string, size = 10, type = "video/mp4") =>
    new File(["x".repeat(size)], name, { type });

  it("refuses files it cannot process and says why", () => {
    const queue = new UploadQueue();
    vi.spyOn(queue as unknown as { pump: () => void }, "pump").mockImplementation(() => undefined);

    const rejected = queue.add(
      [
        video("doc.pdf", 10, "application/pdf"),
        video("vacio.mp4", 0),
        video("enorme.mp4", 200),
        video("bien.mp4", 10),
      ],
      100,
    );

    expect(rejected.map(({ reason }) => reason)).toEqual([
      "no es un video",
      "está vacío",
      "supera el tamaño máximo",
    ]);
    expect(queue.getSnapshot().map((item) => item.title)).toEqual(["bien"]);
  });
});
