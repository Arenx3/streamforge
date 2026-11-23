import { ArrowLeft, CircleAlert, Pencil, Trash2 } from "lucide-react";
import { useCallback, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { Button, buttonClass } from "../../components/ui/Button";
import { Notice } from "../../components/ui/Notice";
import { Perforation } from "../../components/ui/Perforation";
import { ApiError } from "../../lib/api";
import { errorMessage, translate } from "../../lib/errors";
import { formatBytes, formatDuration, formatQuality } from "../../lib/format";
import { VideoPlayer } from "../player/VideoPlayer";
import { isProcessing, useVideo, type VideoDetail } from "./api";
import { StatusMark, statusLabel, statusTone } from "./StatusMark";
import { DeleteVideoDialog, EditVideoDialog } from "./VideoDialogs";

const backLink =
  "text-muted hover:text-bone mb-4 inline-flex items-center gap-1.5 text-[13px] font-medium transition-colors";

function Stage({ video }: { video: VideoDetail }) {
  const failed = video.status === "FAILED";
  const pending = video.status === "PENDING_UPLOAD";
  return (
    <div className="bg-raised border-line-strong flex aspect-video w-full flex-col items-center justify-center rounded-lg border border-dashed px-6 text-center">
      {failed ? (
        <>
          <CircleAlert aria-hidden className="text-danger size-8" />
          <p className="marquee mt-2 text-[34px] leading-9">No se pudo procesar</p>
          <p className="text-bone-soft mt-1 max-w-[52ch]">
            {translate(video.error_message)}
          </p>
        </>
      ) : pending ? (
        <>
          <p className="marquee text-[34px] leading-9">Subida sin terminar</p>
          <p className="text-muted mt-1 max-w-[52ch]">
            Este archivo no llegó completo. Bórralo y vuelve a subirlo.
          </p>
        </>
      ) : (
        <>
          <p className="marquee text-marquee tabular text-[72px] leading-none md:text-[104px]">
            {video.progress} %
          </p>
          <p className="text-bone-soft mt-1">
            {video.status === "UPLOADED" ? "En cola para procesarse" : "Convirtiendo a HLS"}
          </p>
          <Perforation
            value={video.progress}
            tone={statusTone(video.status)}
            label={`Avance de ${video.title}: ${statusLabel(video.status)}`}
            className="mt-5 max-w-[420px]"
          />
        </>
      )}
    </div>
  );
}

function PageSkeleton() {
  return (
    <div role="status" aria-label="Cargando video" className="mx-auto max-w-[1120px]">
      <span className="skeleton mb-4 block h-4 w-24" />
      <span className="skeleton block aspect-video w-full rounded-lg" />
      <span className="skeleton mt-5 block h-9 w-1/2" />
      <span className="skeleton mt-3 block h-4 w-1/3" />
    </div>
  );
}

export function VideoPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const query = useVideo(id);
  const refetch = query.refetch;
  const [dialog, setDialog] = useState<"edit" | "delete" | null>(null);
  // Each response signs a new link: the player keeps the first and asks when it expires.
  const [firstUrl, setFirstUrl] = useState<{ id: string; url: string } | null>(null);
  const playbackUrl = query.data?.id === id ? query.data.playback_url : null;
  if (playbackUrl && firstUrl?.id !== id) setFirstUrl({ id, url: playbackUrl });

  const getFreshUrl = useCallback(async () => {
    const { data } = await refetch();
    return data?.playback_url ?? null;
  }, [refetch]);

  if (query.isPending) return <PageSkeleton />;
  // A failed background refresh keeps the page, and the player, on screen.
  const missing = query.error instanceof ApiError && query.error.status === 404;
  if (query.isLoadingError || missing) {
    return (
      <div className="py-16 text-center">
        <h1 className="marquee text-[44px] leading-none">
          {missing ? "Este video no existe" : "No se pudo cargar"}
        </h1>
        <p className="text-muted mt-2">
          {missing ? "Quizá se borró o pertenece a otra cuenta." : errorMessage(query.error)}
        </p>
        <Link to="/" className={`${buttonClass("primary")} mt-5`}>
          Volver a la biblioteca
        </Link>
      </div>
    );
  }

  const video = query.data;
  const ready = video.status === "READY" && firstUrl?.id === id;
  const facts = [
    formatDuration(video.duration_seconds),
    formatQuality(video.width, video.height),
    formatBytes(video.size_bytes),
    new Intl.DateTimeFormat("es", { dateStyle: "medium" }).format(new Date(video.created_at)),
  ].filter((fact) => fact !== "—");

  return (
    <article className="mx-auto max-w-[1120px]">
      <Link to="/" className={backLink}>
        <ArrowLeft aria-hidden className="size-3.5" />
        Biblioteca
      </Link>

      {ready ? (
        <VideoPlayer
          key={video.id}
          url={firstUrl.url}
          poster={video.thumbnail_url}
          title={video.title}
          getFreshUrl={getFreshUrl}
        />
      ) : (
        <Stage video={video} />
      )}

      <header className="mt-5 flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="min-w-0 flex-1 basis-80">
          <h1 className="marquee text-[36px] leading-[38px] text-balance break-words md:text-[46px] md:leading-[48px]">
            {video.title}
          </h1>
          <p className="text-muted tabular mt-1.5 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-[13px]">
            {!isProcessing(video) && video.status !== "READY" && <StatusMark status={video.status} />}
            {facts.join(" · ")}
          </p>
        </div>
        <div className="flex gap-2">
          <Button variant="secondary" size="sm" onClick={() => setDialog("edit")}>
            <Pencil aria-hidden className="size-3.5" />
            Editar
          </Button>
          <Button variant="secondary" size="sm" onClick={() => setDialog("delete")}>
            <Trash2 aria-hidden className="size-3.5" />
            Borrar
          </Button>
        </div>
      </header>

      {video.description && (
        <p className="text-bone-soft mt-4 max-w-[70ch] text-[15px] leading-6 break-words whitespace-pre-wrap">
          {video.description}
        </p>
      )}
      {query.isRefetchError && (
        <Notice tone="error" className="mt-4">
          {errorMessage(query.error)}
        </Notice>
      )}

      {dialog === "edit" && <EditVideoDialog video={video} onClose={() => setDialog(null)} />}
      {dialog === "delete" && (
        <DeleteVideoDialog
          video={video}
          onClose={() => setDialog(null)}
          onDeleted={() => navigate("/", { replace: true })}
        />
      )}
    </article>
  );
}
