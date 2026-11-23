import { api, ApiError } from "../../lib/api";
import type { Video } from "../videos/api";

type UploadPart = { part_number: number; size: number; url: string };
type CreatedUpload = { video: Video; upload: { parts: UploadPart[] } };

export type UploadCallbacks = {
  onProgress: (loadedBytes: number) => void;
  onFinishing: () => void;
};

const PARALLEL_PARTS = 3;
const MAX_ATTEMPTS = 4;
const RETRY_BASE_MS = 1000;

const CONTENT_TYPES: Record<string, string> = {
  mp4: "video/mp4",
  m4v: "video/x-m4v",
  mov: "video/quicktime",
  mkv: "video/x-matroska",
  webm: "video/webm",
  avi: "video/x-msvideo",
  ts: "video/mp2t",
  flv: "video/x-flv",
  wmv: "video/x-ms-wmv",
};

export const ACCEPTED_FILES = `video/*,${Object.keys(CONTENT_TYPES)
  .map((extension) => `.${extension}`)
  .join(",")}`;

/** Browsers leave the type empty for formats like .mkv, so the extension fills the gap. */
export function contentTypeOf(file: Pick<File, "name" | "type">): string | null {
  if (/^video\/[a-z0-9][a-z0-9.+-]*$/.test(file.type)) return file.type;
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  return CONTENT_TYPES[extension] ?? null;
}

export function titleFrom(fileName: string) {
  const title = fileName.replace(/\.[^.]+$/, "").trim() || fileName.trim();
  return title.slice(0, 255);
}

class PartError extends Error {
  readonly retryable: boolean;

  constructor(status: number) {
    super(`Part upload failed with status ${status}`);
    this.retryable = status === 0 || status === 408 || status === 429 || status >= 500;
  }
}

const abortError = () => new DOMException("Upload canceled", "AbortError");
export const isAbort = (error: unknown) =>
  error instanceof DOMException && error.name === "AbortError";

function wait(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener(
      "abort",
      () => {
        clearTimeout(timer);
        reject(abortError());
      },
      { once: true },
    );
  });
}

function putPart(url: string, body: Blob, signal: AbortSignal, onLoaded: (bytes: number) => void) {
  return new Promise<void>((resolve, reject) => {
    if (signal.aborted) {
      reject(abortError());
      return;
    }
    const request = new XMLHttpRequest();
    const abort = () => request.abort();
    signal.addEventListener("abort", abort, { once: true });
    const settle = (finish: () => void) => {
      signal.removeEventListener("abort", abort);
      finish();
    };
    request.open("PUT", url);
    request.upload.onprogress = (event) => onLoaded(event.loaded);
    request.onload = () =>
      settle(() => {
        if (request.status >= 200 && request.status < 300) resolve();
        else reject(new PartError(request.status));
      });
    request.onerror = () => settle(() => reject(new PartError(0)));
    request.onabort = () => settle(() => reject(abortError()));
    request.send(body);
  });
}

async function sendParts(
  file: File,
  parts: UploadPart[],
  signal: AbortSignal,
  onProgress: (loadedBytes: number) => void,
) {
  const loaded = new Array<number>(parts.length).fill(0);
  const report = () => onProgress(loaded.reduce((sum, bytes) => sum + bytes, 0));
  let offset = 0;
  const pending = parts.map((part, index) => {
    const blob = file.slice(offset, offset + part.size);
    offset += part.size;
    return { part, blob, index };
  });

  const worker = async () => {
    for (let job = pending.shift(); job; job = pending.shift()) {
      for (let attempt = 1; ; attempt += 1) {
        const { part, blob, index } = job;
        try {
          await putPart(part.url, blob, signal, (bytes) => {
            loaded[index] = bytes;
            report();
          });
          loaded[index] = part.size;
          report();
          break;
        } catch (error) {
          loaded[index] = 0;
          if (!(error instanceof PartError) || !error.retryable || attempt >= MAX_ATTEMPTS) {
            throw error;
          }
          await wait(RETRY_BASE_MS * 2 ** (attempt - 1), signal);
        }
      }
    }
  };

  await Promise.all(Array.from({ length: Math.min(PARALLEL_PARTS, parts.length) }, worker));
}

async function complete(videoId: string, signal: AbortSignal) {
  for (let attempt = 1; ; attempt += 1) {
    try {
      return await api<Video>(`/videos/${videoId}/complete/`, { method: "POST", signal });
    } catch (error) {
      const transient =
        error instanceof TypeError || (error instanceof ApiError && error.status >= 500);
      if (!transient || attempt >= MAX_ATTEMPTS) throw error;
      await wait(RETRY_BASE_MS * 2 ** (attempt - 1), signal);
    }
  }
}

/** Sends the file straight to storage. A failed or canceled upload leaves nothing behind. */
export async function uploadVideo(
  file: File,
  signal: AbortSignal,
  { onProgress, onFinishing }: UploadCallbacks,
): Promise<Video> {
  const { video, upload } = await api<CreatedUpload>("/videos/", {
    method: "POST",
    signal,
    body: {
      title: titleFrom(file.name),
      content_type: contentTypeOf(file),
      size_bytes: file.size,
    },
  });
  // One failed part stops the others, so they do not keep sending for nothing.
  const stop = new AbortController();
  const stopAll = () => stop.abort();
  signal.addEventListener("abort", stopAll, { once: true });
  try {
    try {
      await sendParts(file, upload.parts, stop.signal, onProgress);
    } catch (error) {
      stop.abort();
      throw error;
    }
    onFinishing();
    return await complete(video.id, signal);
  } catch (error) {
    await api(`/videos/${video.id}/`, { method: "DELETE" }).catch(() => undefined);
    throw signal.aborted ? abortError() : error;
  } finally {
    signal.removeEventListener("abort", stopAll);
  }
}
