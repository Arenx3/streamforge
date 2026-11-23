import { ChevronLeft, ChevronRight, Pencil, Trash2, UploadCloud } from "lucide-react";
import { useState } from "react";
import { Link, Navigate, useSearchParams } from "react-router";

import { PageHeader } from "../../components/layout/AppShell";
import { Button, buttonClass } from "../../components/ui/Button";
import { Notice } from "../../components/ui/Notice";
import { Perforation } from "../../components/ui/Perforation";
import { ApiError } from "../../lib/api";
import { errorMessage } from "../../lib/errors";
import { formatBytes, formatDuration, formatQuality } from "../../lib/format";
import {
  countFor,
  FILTERS,
  isProcessing,
  PAGE_SIZE,
  useVideos,
  useVideoSummary,
  type Video,
  type VideoFilter,
  type VideoSummary,
} from "./api";
import { StatusMark, statusLabel, statusTone } from "./StatusMark";
import { Thumbnail } from "./Thumbnail";
import { DeleteVideoDialog, EditVideoDialog } from "./VideoDialogs";

const headClass = "text-muted border-line border-b px-3 py-2.5 text-[11px] font-medium tracking-[0.09em] uppercase";
const cellClass = "border-line border-b border-dashed px-3 py-3";
const numberClass = "tabular text-bone-soft text-right whitespace-nowrap";

function plural(count: number, one: string, many: string) {
  return `${count} ${count === 1 ? one : many}`;
}

function summaryText(summary: VideoSummary | undefined) {
  if (!summary) return "Cargando tus videos…";
  const total = Object.values(summary).reduce((sum, count) => sum + count, 0);
  if (total === 0) return "Todavía no tienes videos.";
  const active = summary.UPLOADED + summary.PROCESSING;
  return [
    plural(total, "video", "videos"),
    plural(summary.READY, "listo", "listos"),
    active > 0 && `${active} en proceso`,
    summary.FAILED > 0 && `${summary.FAILED} con error`,
  ]
    .filter(Boolean)
    .join(" · ");
}

function Progress({ video }: { video: Video }) {
  const showPercent = isProcessing(video) || video.status === "FAILED";
  return (
    <span className="flex w-[190px] items-center gap-2.5 xl:w-[290px]">
      <Perforation
        value={video.status === "READY" ? 100 : video.progress}
        tone={statusTone(video.status)}
        label={`Avance de ${video.title}: ${statusLabel(video.status)}`}
        className="min-w-0 flex-1"
      />
      <span className={`tabular w-10 shrink-0 text-right text-[13px] ${showPercent ? "text-bone" : "invisible"}`}>
        {video.progress} %
      </span>
    </span>
  );
}

function VideoRow({
  video,
  onEdit,
  onDelete,
}: {
  video: Video;
  onEdit: () => void;
  onDelete: () => void;
}) {
  return (
    <tr className="group hover:bg-raised/70 transition-colors duration-150">
      <td className={`${cellClass} w-full max-w-0`}>
        <Link to={`/videos/${video.id}`} className="flex min-w-0 items-center gap-3 rounded">
          <Thumbnail url={video.thumbnail_url} className="h-10 w-[72px]" />
          <span className="min-w-0">
            <span className="block truncate font-medium">{video.title}</span>
            <span className="mt-0.5 block text-[13px] md:hidden">
              <StatusMark status={video.status} />
            </span>
            {isProcessing(video) && (
              <span className="mt-1.5 block lg:hidden">
                <Progress video={video} />
              </span>
            )}
          </span>
        </Link>
      </td>
      <td className={`${cellClass} hidden md:table-cell`}>
        <StatusMark status={video.status} />
      </td>
      <td className={`${cellClass} hidden w-[33%] lg:table-cell`}>
        <Progress video={video} />
      </td>
      <td className={`${cellClass} ${numberClass} hidden sm:table-cell`}>
        {formatDuration(video.duration_seconds)}
      </td>
      <td className={`${cellClass} ${numberClass} hidden xl:table-cell`}>
        {formatQuality(video.width, video.height)}
      </td>
      <td className={`${cellClass} ${numberClass} hidden xl:table-cell`}>
        {formatBytes(video.size_bytes)}
      </td>
      <td className={`${cellClass} w-px pr-1 whitespace-nowrap`}>
        <span className="flex justify-end gap-0.5 transition-opacity duration-150 [@media(hover:hover)]:opacity-0 [@media(hover:hover)]:group-focus-within:opacity-100 [@media(hover:hover)]:group-hover:opacity-100">
          <Button variant="ghost" size="icon" aria-label={`Editar ${video.title}`} onClick={onEdit}>
            <Pencil aria-hidden className="size-3.5" />
          </Button>
          <Button variant="ghost" size="icon" aria-label={`Borrar ${video.title}`} onClick={onDelete}>
            <Trash2 aria-hidden className="size-3.5" />
          </Button>
        </span>
      </td>
    </tr>
  );
}

function SkeletonRows() {
  return (
    <div role="status" aria-label="Cargando videos">
      {Array.from({ length: 5 }, (_, index) => (
        <div key={index} className="border-line flex items-center gap-3 border-b border-dashed px-3 py-3">
          <span className="skeleton h-10 w-[72px]" />
          <span className="skeleton h-3.5 w-1/3" />
          <span className="skeleton ml-auto hidden h-3.5 w-1/4 md:block" />
        </div>
      ))}
    </div>
  );
}

function EmptyState({ filter }: { filter: VideoFilter }) {
  if (filter.id !== "todos") {
    return (
      <div className="border-line-strong rounded-xl border border-dashed px-6 py-14 text-center">
        <p className="font-medium">No hay videos en “{filter.label}”.</p>
        <Link to="/" className={`${buttonClass("secondary")} mt-4`}>
          Ver todos
        </Link>
      </div>
    );
  }
  return (
    <div className="border-line-strong rounded-xl border border-dashed px-6 py-16 text-center">
      <p className="marquee text-marquee text-[34px] leading-9">La cartelera está vacía</p>
      <p className="text-muted mx-auto mt-1.5 max-w-[42ch]">
        Sube tu primer video y míralo procesarse en vivo hasta quedar listo para reproducir.
      </p>
      <Link to="/upload" className={`${buttonClass("primary")} mt-5`}>
        <UploadCloud aria-hidden className="size-4" />
        Subir video
      </Link>
    </div>
  );
}

function FilterTabs({ current, summary }: { current: VideoFilter; summary?: VideoSummary }) {
  return (
    <nav aria-label="Filtrar por estado" className="-mx-1 mb-4 flex gap-1 overflow-x-auto px-1 py-0.5 [scrollbar-width:none]">
      {FILTERS.map((filter) => {
        const active = filter.id === current.id;
        const count = countFor(filter, summary);
        return (
          <Link
            key={filter.id}
            to={filter.id === "todos" ? "/" : `/?estado=${filter.id}`}
            aria-current={active ? "page" : undefined}
            className={`press inline-flex h-8 shrink-0 items-center gap-1.5 rounded-md border px-3 text-[13px] font-medium ${
              active
                ? "border-line-strong bg-overlay text-bone"
                : "text-muted hover:text-bone border-transparent"
            }`}
          >
            {filter.label}
            {count !== undefined && <span className="tabular text-muted text-xs">{count}</span>}
          </Link>
        );
      })}
    </nav>
  );
}

type DialogState = { kind: "edit" | "delete"; video: Video } | null;

export function LibraryPage() {
  const [params, setParams] = useSearchParams();
  const filter = FILTERS.find(({ id }) => id === params.get("estado")) ?? FILTERS[0];
  const page = Math.max(1, Number.parseInt(params.get("pagina") ?? "1", 10) || 1);
  const videos = useVideos(filter, page);
  const summary = useVideoSummary();
  const [dialog, setDialog] = useState<DialogState>(null);

  const goToPage = (next: number) => {
    const search = new URLSearchParams(params);
    if (next > 1) search.set("pagina", String(next));
    else search.delete("pagina");
    setParams(search);
  };

  if (videos.error instanceof ApiError && videos.error.status === 404 && page > 1) {
    const search = new URLSearchParams(params);
    search.delete("pagina");
    return <Navigate to={`/?${search}`} replace />;
  }

  const data = videos.data;
  const totalPages = data ? Math.max(1, Math.ceil(data.count / PAGE_SIZE)) : 1;

  return (
    <>
      <PageHeader
        title="Biblioteca"
        summary={summaryText(summary.data)}
        action={
          <Link to="/upload" className={buttonClass("primary")}>
            <UploadCloud aria-hidden className="size-4" />
            Subir video
          </Link>
        }
      />
      <FilterTabs current={filter} summary={summary.data} />

      {videos.isPending && <SkeletonRows />}
      {videos.isError && !data && (
        <Notice tone="error">
          {errorMessage(videos.error)}{" "}
          <button type="button" className="font-medium underline" onClick={() => void videos.refetch()}>
            Reintentar
          </button>
        </Notice>
      )}
      {data?.results.length === 0 && <EmptyState filter={filter} />}
      {data && data.results.length > 0 && (
        <table className="w-full table-auto border-collapse text-left">
          <caption className="sr-only">Tus videos</caption>
          <thead>
            <tr>
              <th scope="col" className={headClass}>Video</th>
              <th scope="col" className={`${headClass} hidden md:table-cell`}>Estado</th>
              <th scope="col" className={`${headClass} hidden lg:table-cell`}>Avance</th>
              <th scope="col" className={`${headClass} hidden text-right sm:table-cell`}>Duración</th>
              <th scope="col" className={`${headClass} hidden text-right xl:table-cell`}>Calidad</th>
              <th scope="col" className={`${headClass} hidden text-right xl:table-cell`}>Tamaño</th>
              <th scope="col" className={headClass}>
                <span className="sr-only">Acciones</span>
              </th>
            </tr>
          </thead>
          <tbody>
            {data.results.map((video) => (
              <VideoRow
                key={video.id}
                video={video}
                onEdit={() => setDialog({ kind: "edit", video })}
                onDelete={() => setDialog({ kind: "delete", video })}
              />
            ))}
          </tbody>
        </table>
      )}

      {totalPages > 1 && (
        <nav aria-label="Paginación" className="mt-4 flex items-center justify-end gap-2">
          <span className="tabular text-muted mr-1 text-[13px]">
            Página {page} de {totalPages}
          </span>
          <Button variant="secondary" size="icon" aria-label="Página anterior" disabled={page <= 1} onClick={() => goToPage(page - 1)}>
            <ChevronLeft aria-hidden className="size-4" />
          </Button>
          <Button variant="secondary" size="icon" aria-label="Página siguiente" disabled={!data?.next} onClick={() => goToPage(page + 1)}>
            <ChevronRight aria-hidden className="size-4" />
          </Button>
        </nav>
      )}

      {dialog?.kind === "edit" && (
        <EditVideoDialog video={dialog.video} onClose={() => setDialog(null)} />
      )}
      {dialog?.kind === "delete" && (
        <DeleteVideoDialog video={dialog.video} onClose={() => setDialog(null)} />
      )}
    </>
  );
}
