import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";

import type { Page, Video, VideoDetail } from "./api";
import { createSseParser } from "./liveEvents";
import { applyVideoEvent } from "./LiveUpdates";

describe("createSseParser", () => {
  const parse = (...chunks: string[]) => {
    const events: [string, string][] = [];
    const feed = createSseParser((event, data) => events.push([event, data]));
    chunks.forEach(feed);
    return events;
  };

  it("reads named events", () => {
    expect(parse('event: video\ndata: {"id": "a"}\n\n')).toEqual([["video", '{"id": "a"}']]);
  });

  it("handles events split across network chunks", () => {
    expect(parse("event: vi", "deo\nda", 'ta: {"id"', ': "a"}\n', "\n")).toEqual([
      ["video", '{"id": "a"}'],
    ]);
  });

  it("reads several events from one chunk", () => {
    expect(parse("event: ready\ndata: {}\n\nevent: video\ndata: 1\n\n")).toEqual([
      ["ready", "{}"],
      ["video", "1"],
    ]);
  });

  it("ignores keepalives and control fields", () => {
    expect(parse(": keepalive\n\nretry: 3000\n\n")).toEqual([]);
  });

  it("waits for the blank line that ends an event", () => {
    expect(parse("event: video\ndata: 1\n")).toEqual([]);
  });
});

describe("applyVideoEvent", () => {
  const video = { id: "a", title: "Demo", status: "PROCESSING", progress: 10 } as Video;
  const other = { id: "b", title: "Otro", status: "READY", progress: 100 } as Video;

  function setup() {
    const client = new QueryClient();
    client.setQueryData<Page<Video>>(["videos", "list", "todos", 1], {
      count: 2,
      next: null,
      previous: null,
      results: [video, other],
    });
    client.setQueryData<VideoDetail>(["videos", "detail", "a"], {
      ...video,
      error_message: "",
      processed_at: null,
      playback_url: null,
    });
    return { client, invalidate: vi.spyOn(client, "invalidateQueries") };
  }

  const event = {
    id: "a",
    status: "PROCESSING",
    progress: 47,
    error_message: "",
  } as const;

  it("updates progress in place without refetching", () => {
    const { client, invalidate } = setup();

    applyVideoEvent(client, { ...event, kind: "progress" });

    const page = client.getQueryData<Page<Video>>(["videos", "list", "todos", 1])!;
    expect(page.results[0]!.progress).toBe(47);
    expect(page.results[1]!.progress).toBe(100);
    expect(client.getQueryData<VideoDetail>(["videos", "detail", "a"])!.progress).toBe(47);
    expect(invalidate).not.toHaveBeenCalled();
  });

  it("keeps an outdated list marked as stale", async () => {
    const { client } = setup();
    const key = ["videos", "list", "todos", 1];
    await client.invalidateQueries({ queryKey: key, refetchType: "none" });

    applyVideoEvent(client, { ...event, kind: "progress" });
    applyVideoEvent(client, { ...event, id: "not-in-this-page", kind: "progress" });

    const query = client.getQueryCache().find({ queryKey: key })!;
    expect(query.state.isInvalidated).toBe(true);
    expect((query.state.data as Page<Video>).results[0]!.progress).toBe(47);
  });

  it("does not make cached data look newly fetched", () => {
    const { client } = setup();
    const key = ["videos", "list", "todos", 1];
    const before = client.getQueryCache().find({ queryKey: key })!.state.dataUpdatedAt;
    vi.spyOn(Date, "now").mockReturnValue(before + 60_000);

    applyVideoEvent(client, { ...event, kind: "progress" });

    expect(client.getQueryCache().find({ queryKey: key })!.state.dataUpdatedAt).toBe(before);
  });

  it("refetches when the state changes", () => {
    const { client, invalidate } = setup();

    applyVideoEvent(client, { ...event, kind: "state", status: "READY", progress: 100 });

    expect(invalidate).toHaveBeenCalledWith({ queryKey: ["videos"] });
  });

  it("drops a deleted video", () => {
    const { client, invalidate } = setup();

    applyVideoEvent(client, { id: "a", kind: "deleted" });

    expect(client.getQueryData(["videos", "detail", "a"])).toBeUndefined();
    expect(invalidate).toHaveBeenCalled();
  });
});
