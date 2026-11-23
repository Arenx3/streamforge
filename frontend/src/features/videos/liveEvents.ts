import { useSyncExternalStore } from "react";

import { api, ApiError, freshAccessToken } from "../../lib/api";

export type VideoEvent =
  | { id: string; kind: "deleted" }
  | {
      id: string;
      kind: "state" | "progress";
      status: "PENDING_UPLOAD" | "UPLOADED" | "PROCESSING" | "READY" | "FAILED";
      progress: number;
      error_message: string;
    };

type Handlers = { onReady: () => void; onVideo: (event: VideoEvent) => void };

const MAX_RETRY_MS = 30_000;

let live = false;
const liveListeners = new Set<() => void>();

function setLive(value: boolean) {
  if (live === value) return;
  live = value;
  liveListeners.forEach((listener) => listener());
}

/** While true, updates arrive by push and polling can stop. */
export function useLiveConnection() {
  return useSyncExternalStore(
    (listener) => {
      liveListeners.add(listener);
      return () => liveListeners.delete(listener);
    },
    () => live,
  );
}

export function createSseParser(onEvent: (event: string, data: string) => void) {
  let buffer = "";
  let event = "message";
  let data: string[] = [];

  return (chunk: string) => {
    buffer += chunk;
    const lines = buffer.split(/\r\n|\n|\r/);
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      if (line === "") {
        if (data.length) onEvent(event, data.join("\n"));
        event = "message";
        data = [];
      } else if (!line.startsWith(":")) {
        const colon = line.indexOf(":");
        const field = colon === -1 ? line : line.slice(0, colon);
        const value = colon === -1 ? "" : line.slice(colon + 1).replace(/^ /, "");
        if (field === "event") event = value;
        else if (field === "data") data.push(value);
      }
    }
  };
}

/**
 * Keeps one stream open while the tab is visible. Hidden tabs disconnect because
 * browsers allow only six connections per host over HTTP/1.1.
 */
export function connectVideoEvents({ onReady, onVideo }: Handlers) {
  let stopped = false;
  let controller: AbortController | null = null;
  let retryTimer: ReturnType<typeof setTimeout> | undefined;
  let failures = 0;

  const schedule = (delay: number) => {
    clearTimeout(retryTimer);
    retryTimer = setTimeout(() => void run(), delay);
  };

  const dispatch = (event: string, data: string) => {
    if (event === "ready") {
      failures = 0;
      setLive(true);
      onReady();
    } else if (event === "video") {
      onVideo(JSON.parse(data) as VideoEvent);
    }
  };

  const run = async () => {
    if (stopped || controller || document.hidden) return;
    const current = new AbortController();
    controller = current;
    let clean = false;
    try {
      const token = await freshAccessToken();
      if (!token) return;
      const response = await fetch("/api/videos/events/", {
        headers: { Accept: "text/event-stream", Authorization: `Bearer ${token}` },
        signal: current.signal,
      });
      if (!response.ok || !response.body) {
        // A revoked token: this request renews it or ends the session.
        if (response.status === 401) await api("/auth/me/").catch(() => undefined);
        throw new ApiError(response.status, null);
      }
      const parse = createSseParser(dispatch);
      const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        parse(value);
      }
      clean = true;
    } catch {
      // Handled below: any failure ends in a delayed reconnect.
    } finally {
      controller = null;
      setLive(false);
      if (!stopped && !current.signal.aborted) {
        failures = clean ? 0 : failures + 1;
        schedule(clean ? 0 : Math.min(1000 * 2 ** (failures - 1), MAX_RETRY_MS));
      }
    }
  };

  const onVisibility = () => {
    if (document.hidden) {
      clearTimeout(retryTimer);
      controller?.abort();
    } else {
      void run();
    }
  };

  document.addEventListener("visibilitychange", onVisibility);
  void run();

  return () => {
    stopped = true;
    clearTimeout(retryTimer);
    document.removeEventListener("visibilitychange", onVisibility);
    controller?.abort();
    setLive(false);
  };
}
