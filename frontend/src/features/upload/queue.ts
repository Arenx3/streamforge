import { useSyncExternalStore } from "react";

import { ApiError } from "../../lib/api";
import { errorMessage } from "../../lib/errors";
import type { Video } from "../videos/api";
import { contentTypeOf, isAbort, titleFrom, uploadVideo } from "./uploader";

export type UploadStatus = "queued" | "uploading" | "finishing" | "done" | "failed";

export type UploadItem = {
  id: string;
  file: File;
  title: string;
  status: UploadStatus;
  loaded: number;
  bytesPerSecond: number;
  error?: string;
  videoId?: string;
};

type FinishListener = (video: Video) => void;

const SPEED_WINDOW_MS = 4000;
export const isActive = (item: UploadItem) =>
  item.status === "uploading" || item.status === "finishing";
export const isPending = (item: UploadItem) => item.status === "queued" || isActive(item);
export const percentOf = (item: UploadItem) =>
  item.file.size > 0 ? Math.min(100, (item.loaded / item.file.size) * 100) : 0;

/** Lives outside React so uploads keep running while the user moves around the app. */
export class UploadQueue {
  private items: UploadItem[] = [];
  private readonly listeners = new Set<() => void>();
  private readonly finishListeners = new Set<FinishListener>();
  private controller: AbortController | null = null;
  private nextId = 1;

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getSnapshot = () => this.items;

  onFinish(listener: FinishListener) {
    this.finishListeners.add(listener);
    return () => {
      this.finishListeners.delete(listener);
    };
  }

  /** Returns the names of the files it refused, with the reason. */
  add(files: Iterable<File>, maxBytes: number) {
    const rejected: { name: string; reason: string }[] = [];
    const accepted: UploadItem[] = [];
    for (const file of files) {
      if (!contentTypeOf(file)) rejected.push({ name: file.name, reason: "no es un video" });
      else if (file.size === 0) rejected.push({ name: file.name, reason: "está vacío" });
      else if (file.size > maxBytes) rejected.push({ name: file.name, reason: "supera el tamaño máximo" });
      else {
        accepted.push({
          id: String(this.nextId++),
          file,
          title: titleFrom(file.name),
          status: "queued",
          loaded: 0,
          bytesPerSecond: 0,
        });
      }
    }
    if (accepted.length) {
      this.set([...accepted.reverse(), ...this.items]);
      this.pump();
    }
    return rejected;
  }

  cancel(id: string) {
    const item = this.items.find((candidate) => candidate.id === id);
    if (!item) return;
    if (isActive(item)) this.controller?.abort();
    this.set(this.items.filter((candidate) => candidate.id !== id));
  }

  retry(id: string) {
    this.patch(id, { status: "queued", loaded: 0, bytesPerSecond: 0, error: undefined });
    this.pump();
  }

  dismiss(id: string) {
    this.set(this.items.filter((candidate) => candidate.id !== id));
  }

  /** Called when the session ends: uploads belong to the account that started them. */
  reset() {
    this.controller?.abort();
    this.set([]);
  }

  private set(items: UploadItem[]) {
    this.items = items;
    this.listeners.forEach((listener) => listener());
  }

  private patch(id: string, changes: Partial<UploadItem>) {
    if (!this.items.some((item) => item.id === id)) return;
    this.set(this.items.map((item) => (item.id === id ? { ...item, ...changes } : item)));
  }

  private pump() {
    if (this.controller) return;
    const next = [...this.items].reverse().find((item) => item.status === "queued");
    if (next) void this.run(next);
  }

  private async run(item: UploadItem) {
    const controller = new AbortController();
    this.controller = controller;
    this.patch(item.id, { status: "uploading" });
    const samples: { at: number; bytes: number }[] = [{ at: performance.now(), bytes: 0 }];
    let highest = 0;
    try {
      const video = await uploadVideo(item.file, controller.signal, {
        onProgress: (bytes) => {
          // A retried part starts over, but the meter never moves backwards.
          highest = Math.max(highest, bytes);
          const now = performance.now();
          samples.push({ at: now, bytes });
          while (samples.length > 2 && now - samples[0]!.at > SPEED_WINDOW_MS) samples.shift();
          const first = samples[0]!;
          const seconds = (now - first.at) / 1000;
          this.patch(item.id, {
            loaded: highest,
            bytesPerSecond: seconds > 0.25 ? Math.max(0, (bytes - first.bytes) / seconds) : 0,
          });
        },
        onFinishing: () =>
          this.patch(item.id, { status: "finishing", loaded: item.file.size, bytesPerSecond: 0 }),
      });
      this.patch(item.id, { status: "done", loaded: item.file.size, videoId: video.id });
      this.finishListeners.forEach((listener) => listener(video));
    } catch (error) {
      if (!isAbort(error)) {
        const blocked = error instanceof ApiError && error.status === 403;
        this.patch(item.id, {
          status: "failed",
          bytesPerSecond: 0,
          error: blocked ? "Confirma tu correo para poder subir videos." : errorMessage(error),
        });
      }
    } finally {
      this.controller = null;
      this.pump();
    }
  }
}

export const uploads = new UploadQueue();

export const useUploads = () => useSyncExternalStore(uploads.subscribe, uploads.getSnapshot);
