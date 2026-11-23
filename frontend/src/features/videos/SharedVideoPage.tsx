import { useCallback, useEffect, useState, type CSSProperties, type ReactNode } from "react";
import { Link, useParams } from "react-router";

import { Button, buttonClass } from "../../components/ui/Button";
import { PerforationRule } from "../../components/ui/Perforation";
import { ApiError } from "../../lib/api";
import { errorMessage } from "../../lib/errors";
import { formatDuration, formatQuality } from "../../lib/format";
import { VideoPlayer } from "../player/VideoPlayer";
import { useSharedVideo } from "./api";

const HEADER = "56px";

function Stub({ title, note, children }: { title: string; note: string; children: ReactNode }) {
  return (
    <div className="px-4 pt-[12vh]">
      <div className="ticket bg-raised mx-auto max-w-[560px]">
        <div className="px-6 pt-6 pb-4">
          <h1 className="marquee text-[40px] leading-[42px] text-balance md:text-[52px] md:leading-[54px]">
            {title}
          </h1>
          <p className="text-bone-soft mt-2">{note}</p>
        </div>
        <div className="ticket-tear px-6 py-1">
          <PerforationRule />
        </div>
        <div className="px-6 pt-3 pb-5">{children}</div>
      </div>
    </div>
  );
}

export function SharedVideoPage() {
  const { token = "" } = useParams();
  const query = useSharedVideo(token);
  const { refetch } = query;
  // Each response signs a new link: the player keeps the first and asks when it expires.
  const [first, setFirst] = useState<{ token: string; url: string } | null>(null);
  const url = query.data?.playback_url ?? null;
  if (url && first?.token !== token) setFirst({ token, url });

  const getFreshUrl = useCallback(async () => {
    const { data, isError } = await refetch();
    return isError ? null : (data?.playback_url ?? null);
  }, [refetch]);

  const title = query.data?.title;
  useEffect(() => {
    if (!title) return;
    const previous = document.title;
    document.title = `${title} · StreamForge`;
    return () => {
      document.title = previous;
    };
  }, [title]);

  const gone =
    (query.error instanceof ApiError && query.error.status === 404) ||
    (query.isSuccess && !query.data.playback_url);

  const content = () => {
    if (gone) {
      return (
        <Stub
          title="Función cancelada"
          note="Este enlace ya no está disponible. Pídele uno nuevo a quien te lo compartió."
        >
          <Link to="/" className={buttonClass("primary")}>
            Conocer StreamForge
          </Link>
        </Stub>
      );
    }
    if (query.isPending) {
      return (
        <div role="status" aria-label="Cargando video">
          <span className="skeleton block aspect-video max-h-[calc(100dvh-var(--player-offset))] w-full rounded-none" />
        </div>
      );
    }
    if (query.isLoadingError || first?.token !== token) {
      return (
        <Stub title="No se pudo cargar" note={errorMessage(query.error)}>
          <Button onClick={() => void refetch()}>Reintentar</Button>
        </Stub>
      );
    }
    const video = query.data;
    const facts = [
      formatDuration(video.duration_seconds),
      formatQuality(video.width, video.height),
    ]
      .filter((fact) => fact !== "—")
      .join(" · ");
    return (
      <article>
        <VideoPlayer
          key={token}
          url={first.url}
          poster={video.thumbnail_url}
          title={video.title}
          getFreshUrl={getFreshUrl}
        />
        <div className="px-4 pt-4 pb-8 md:px-9 md:pt-5">
          <h1 className="marquee text-[36px] leading-[38px] text-balance break-words md:text-[46px] md:leading-[48px]">
            {video.title}
          </h1>
          {facts && <p className="text-muted tabular mt-1.5 text-[13px]">{facts}</p>}
          {video.description && (
            <p className="text-bone-soft mt-4 max-w-[70ch] text-[15px] leading-6 break-words whitespace-pre-wrap">
              {video.description}
            </p>
          )}
        </div>
      </article>
    );
  };

  return (
    <div className="min-h-dvh" style={{ "--player-offset": HEADER } as CSSProperties}>
      <meta name="robots" content="noindex, nofollow" />
      <header className="app-rail bg-rail border-rail-line sticky top-0 z-30 flex h-14 items-center justify-between gap-3 border-b px-4 md:px-9">
        <Link to="/" className="marquee text-marquee text-[28px] leading-8">
          StreamForge
        </Link>
        {!gone && (
          <Link to="/" className={buttonClass("secondary", "sm")}>
            Probar StreamForge
          </Link>
        )}
      </header>
      <main id="contenido">{content()}</main>
    </div>
  );
}
