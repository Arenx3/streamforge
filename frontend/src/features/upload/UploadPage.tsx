import { useQueryClient } from "@tanstack/react-query";
import { CircleCheck, RotateCcw, UploadCloud, X } from "lucide-react";
import { useEffect, useRef, useState, type DragEvent } from "react";
import { Link, useLocation } from "react-router";
import { toast } from "sonner";

import { PageHeader } from "../../components/layout/AppShell";
import { Button, buttonClass } from "../../components/ui/Button";
import { Perforation, type PerforationTone } from "../../components/ui/Perforation";
import { onSessionEnd } from "../../lib/api";
import { formatBytes, formatDuration } from "../../lib/format";
import { useAuth, useConfig } from "../auth/AuthProvider";
import {
  isActive,
  isPending,
  percentOf,
  uploads,
  useUploads,
  type UploadItem,
  type UploadStatus,
} from "./queue";
import { ACCEPTED_FILES } from "./uploader";

const FALLBACK_MAX_BYTES = 5 * 1024 ** 3;

const STATUS_TEXT: Record<UploadStatus, string> = {
  queued: "En espera",
  uploading: "Subiendo",
  finishing: "Uniendo partes",
  done: "Subido",
  failed: "Falló",
};

const TONES: Record<UploadStatus, PerforationTone> = {
  queued: "idle",
  uploading: "live",
  finishing: "live",
  done: "done",
  failed: "failed",
};

function detailOf(item: UploadItem) {
  if (item.status === "failed") return item.error;
  if (item.status === "uploading" && item.bytesPerSecond > 0) {
    const remaining = (item.file.size - item.loaded) / item.bytesPerSecond;
    return `${formatBytes(item.bytesPerSecond)}/s · queda ${formatDuration(remaining)}`;
  }
  if (item.status === "done") return "Ya está en tu biblioteca";
  return formatBytes(item.file.size);
}

function UploadRow({ item }: { item: UploadItem }) {
  const percent = percentOf(item);
  return (
    <li className="border-line grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-2 border-b border-dashed py-3.5">
      <div className="min-w-0">
        <p className="truncate font-medium">{item.title}</p>
        <p className={`mt-0.5 truncate text-[13px] ${item.status === "failed" ? "text-danger" : "text-muted"}`}>
          <span className={item.status === "failed" ? "" : "text-bone-soft"}>
            {STATUS_TEXT[item.status]}
          </span>
          {" · "}
          {detailOf(item)}
        </p>
      </div>
      <div className="flex items-center gap-1">
        {item.status === "done" && item.videoId && (
          <Link to={`/videos/${item.videoId}`} className={buttonClass("secondary", "sm")}>
            <CircleCheck aria-hidden className="text-ok size-3.5" />
            Ver video
          </Link>
        )}
        {item.status === "failed" && (
          <Button variant="secondary" size="sm" onClick={() => uploads.retry(item.id)}>
            <RotateCcw aria-hidden className="size-3.5" />
            Reintentar
          </Button>
        )}
        <Button
          variant="ghost"
          size="icon"
          aria-label={isPending(item) ? `Cancelar ${item.title}` : `Quitar ${item.title}`}
          title={isPending(item) ? "Cancelar" : "Quitar de la lista"}
          onClick={() => (isPending(item) ? uploads.cancel(item.id) : uploads.dismiss(item.id))}
        >
          <X aria-hidden className="size-4" />
        </Button>
      </div>
      <div className="col-span-2 flex items-center gap-3">
        <Perforation
          value={percent}
          tone={TONES[item.status]}
          label={`Subida de ${item.title}`}
          className="min-w-0 flex-1"
        />
        <span className="tabular text-bone-soft w-11 shrink-0 text-right text-[13px]">
          {Math.floor(percent)} %
        </span>
      </div>
    </li>
  );
}

export function UploadPage() {
  const { user } = useAuth();
  const { data: config } = useConfig();
  const items = useUploads();
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const maxBytes = config?.max_upload_bytes ?? FALLBACK_MAX_BYTES;
  const blocked = user ? !user.email_verified : false;

  const addFiles = (files: FileList | null) => {
    if (!files?.length || blocked) return;
    for (const { name, reason } of uploads.add(Array.from(files), maxBytes)) {
      toast.error(`${name} ${reason}.`);
    }
  };

  const onDrop = (event: DragEvent) => {
    event.preventDefault();
    setDragging(false);
    addFiles(event.dataTransfer.files);
  };

  return (
    <>
      <PageHeader
        title="Subir video"
        summary={`Hasta ${formatBytes(maxBytes)} por archivo. Va directo al almacenamiento, sin pasar por la API.`}
      />
      <div
        onDragOver={(event) => {
          event.preventDefault();
          if (!blocked) setDragging(true);
        }}
        onDragLeave={(event) => {
          if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setDragging(false);
        }}
        onDrop={onDrop}
        className={`rounded-xl border-2 border-dashed px-6 py-14 text-center transition-colors duration-150 md:py-20 ${
          dragging ? "border-brass bg-brass/10" : "border-line-strong bg-raised/40"
        } ${blocked ? "opacity-60" : ""}`}
      >
        <UploadCloud aria-hidden className={`mx-auto size-8 ${dragging ? "text-brass" : "text-muted"}`} />
        <p className="marquee mt-3 text-[32px] leading-9">
          {dragging ? "Suéltalos aquí" : "Arrastra tus videos aquí"}
        </p>
        <p className="text-muted mt-1">MP4, MOV, MKV, WebM, AVI, TS, FLV o WMV</p>
        <input
          ref={input}
          type="file"
          multiple
          accept={ACCEPTED_FILES}
          className="sr-only"
          tabIndex={-1}
          aria-label="Elegir archivos de video"
          disabled={blocked}
          onChange={(event) => {
            addFiles(event.target.files);
            event.target.value = "";
          }}
        />
        <Button className="mt-5" disabled={blocked} onClick={() => input.current?.click()}>
          Elegir archivos
        </Button>
      </div>

      {items.length > 0 && (
        <section aria-label="Subidas" className="mt-6">
          <ul>
            {items.map((item) => (
              <UploadRow key={item.id} item={item} />
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

/** Keeps the app in sync with uploads wherever the user is. */
export function UploadWatcher() {
  const client = useQueryClient();
  const items = useUploads();
  const uploading = items.some(isPending);

  useEffect(
    () =>
      uploads.onFinish((video) => {
        void client.invalidateQueries({ queryKey: ["videos"] });
        toast.success(`${video.title} se subió y empezó a procesarse`);
      }),
    [client],
  );

  useEffect(() => onSessionEnd(() => uploads.reset()), []);

  useEffect(() => {
    if (!uploading) return;
    const warn = (event: BeforeUnloadEvent) => event.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [uploading]);

  return null;
}

/** A small progress card while an upload runs and the user is elsewhere. */
export function UploadDock() {
  const items = useUploads();
  const { pathname } = useLocation();
  const current = items.find(isActive);
  if (!current || pathname === "/upload") return null;
  const percent = percentOf(current);
  const waiting = items.filter((item) => item.status === "queued").length;

  return (
    <Link
      to="/upload"
      className="bg-overlay border-line-strong shadow-float fixed right-4 bottom-4 left-4 z-40 block rounded-lg border p-3 md:right-auto md:left-[248px] md:w-[320px]"
    >
      <span className="flex justify-between gap-3 text-[13px]">
        <span className="truncate font-medium">{current.title}</span>
        <span className="tabular text-bone-soft shrink-0">{Math.floor(percent)} %</span>
      </span>
      <Perforation value={percent} label={`Subida de ${current.title}`} className="mt-2" />
      {waiting > 0 && <span className="text-muted mt-1.5 block text-xs">{waiting} en espera</span>}
    </Link>
  );
}
