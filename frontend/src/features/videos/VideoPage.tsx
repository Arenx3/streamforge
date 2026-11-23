import { ArrowLeft, Pencil, Share2, Trash2, UploadCloud } from "lucide-react";
import { useCallback, useState } from "react";
import { Link, useNavigate, useParams } from "react-router";

import { Button, buttonClass } from "../../components/ui/Button";
import { Notice } from "../../components/ui/Notice";
import { ApiError } from "../../lib/api";
import { errorMessage, translate } from "../../lib/errors";
import { formatBytes, formatDuration, formatQuality } from "../../lib/format";
import { VideoPlayer } from "../player/VideoPlayer";
import { useVideo, type VideoDetail } from "./api";
import { DeleteVideoDialog, EditVideoDialog, ShareVideoDialog } from "./VideoDialogs";
import { serialOf, videoJourney, VideoTicket } from "./VideoTicket";

const backLink =
  "text-muted hover:text-bone mb-4 inline-flex items-center gap-1.5 text-[13px] font-medium transition-colors";

function statusNote(video: VideoDetail) {
  switch (video.status) {
    case "PENDING_UPLOAD":
      return "Este archivo no llegó completo. Bórralo y vuelve a subirlo.";
    case "UPLOADED":
      return "En cola. Empieza cuando un worker quede libre.";
    case "PROCESSING":
      return "Convirtiendo a HLS. Puedes salir de esta página: el proceso sigue.";
    case "FAILED":
      return translate(video.error_message);
    case "READY":
      return "Preparando la reproducción…";
  }
}

type TicketProps = {
  video: VideoDetail;
  facts: string;
  onEdit: () => void;
  onDelete: () => void;
};

function PipelineTicket({ video, facts, onEdit, onDelete }: TicketProps) {
  const failed = video.status === "FAILED";
  const stalled = failed || video.status === "PENDING_UPLOAD";
  return (
    <VideoTicket
      heading="h1"
      size="lg"
      title={video.title}
      serial={serialOf(video.id)}
      journey={videoJourney(video)}
    >
      <div className="min-w-0 flex-1 basis-64">
        <p className={failed ? "text-danger font-medium" : "text-bone-soft"}>{statusNote(video)}</p>
        {facts && <p className="text-muted tabular mt-0.5 text-[13px]">{facts}</p>}
      </div>
      <div className="flex flex-wrap gap-2">
        {stalled && (
          <Link to="/upload" className={buttonClass("primary", "sm")}>
            <UploadCloud aria-hidden className="size-3.5" />
            Subir de nuevo
          </Link>
        )}
        <Button variant="secondary" size="sm" onClick={onEdit}>
          <Pencil aria-hidden className="size-3.5" />
          Editar
        </Button>
        <Button variant="secondary" size="sm" onClick={onDelete}>
          <Trash2 aria-hidden className="size-3.5" />
          Borrar
        </Button>
      </div>
    </VideoTicket>
  );
}

function PageSkeleton() {
  return (
    <div role="status" aria-label="Cargando video" className="-mx-4 -mt-6 md:-mx-9 md:-mt-8">
      <span className="skeleton block aspect-video max-h-dvh w-full rounded-none" />
      <div className="px-4 pt-5 md:px-9">
        <span className="skeleton block h-9 w-1/2" />
        <span className="skeleton mt-3 block h-4 w-1/3" />
      </div>
    </div>
  );
}

export function VideoPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const query = useVideo(id);
  const refetch = query.refetch;
  const [dialog, setDialog] = useState<"edit" | "delete" | "share" | null>(null);
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
  ]
    .filter((fact) => fact !== "—")
    .join(" · ");
  const description = video.description && (
    <p className="text-bone-soft mt-4 max-w-[70ch] text-[15px] leading-6 break-words whitespace-pre-wrap">
      {video.description}
    </p>
  );
  const refreshError = query.isRefetchError && (
    <Notice tone="error" className="mt-4">
      {errorMessage(query.error)}
    </Notice>
  );
  const dialogs = (
    <>
      {dialog === "edit" && <EditVideoDialog video={video} onClose={() => setDialog(null)} />}
      {dialog === "share" && <ShareVideoDialog video={video} onClose={() => setDialog(null)} />}
      {dialog === "delete" && (
        <DeleteVideoDialog
          video={video}
          onClose={() => setDialog(null)}
          onDeleted={() => navigate("/", { replace: true })}
        />
      )}
    </>
  );

  if (!ready) {
    return (
      <article className="mx-auto max-w-[760px] md:pt-[6vh]">
        <Link to="/" className={backLink}>
          <ArrowLeft aria-hidden className="size-3.5" />
          Biblioteca
        </Link>
        <PipelineTicket
          video={video}
          facts={facts}
          onEdit={() => setDialog("edit")}
          onDelete={() => setDialog("delete")}
        />
        {description}
        {refreshError}
        {dialogs}
      </article>
    );
  }

  return (
    <article className="-mx-4 -mt-6 md:-mx-9 md:-mt-8">
      <VideoPlayer
        key={video.id}
        url={firstUrl.url}
        poster={video.thumbnail_url}
        title={video.title}
        getFreshUrl={getFreshUrl}
      />
      <div className="px-4 pt-4 pb-8 md:px-9 md:pt-5">
        <header className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
          <div className="min-w-0 flex-1 basis-80">
            <h1 className="marquee text-[36px] leading-[38px] text-balance break-words md:text-[46px] md:leading-[48px]">
              {video.title}
            </h1>
            <p className="text-muted tabular mt-1.5 text-[13px]">{facts}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Link to="/" className={buttonClass("ghost", "sm")}>
              <ArrowLeft aria-hidden className="size-3.5" />
              Biblioteca
            </Link>
            <Button
              variant={video.share_token ? "secondary" : "primary"}
              size="sm"
              onClick={() => setDialog("share")}
            >
              <Share2 aria-hidden className="size-3.5" />
              {video.share_token ? "Compartido" : "Compartir"}
            </Button>
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
        {description}
        {refreshError}
      </div>
      {dialogs}
    </article>
  );
}
