import type Hls from "hls.js";
import {
  LoaderCircle,
  Maximize,
  Minimize,
  Pause,
  Play,
  RotateCcw,
  Volume2,
  VolumeX,
} from "lucide-react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type CSSProperties,
  type KeyboardEvent,
} from "react";

import { Button } from "../../components/ui/Button";
import { fitDots } from "../../components/ui/Perforation";
import { formatDuration } from "../../lib/format";

const AUTO_LEVEL = -1;
const SEEK_SECONDS = 5;
const VOLUME_STEP = 0.1;
const HIDE_CONTROLS_MS = 2500;
const MAX_RECOVERIES = 3;
const RECOVERED_AFTER_SECONDS = 1;

export type QualityLevel = { index: number; height: number };

export function qualityOptions(levels: QualityLevel[], active: number, selected: number) {
  const current = levels.find(({ index }) => index === active);
  const auto = selected === AUTO_LEVEL && current ? `Auto (${current.height}p)` : "Auto";
  return [
    { value: AUTO_LEVEL, label: auto },
    ...[...levels]
      .sort((a, b) => b.height - a.height)
      .map(({ index, height }) => ({ value: index, label: `${height}p` })),
  ];
}

type Props = {
  url: string;
  poster?: string | null;
  title: string;
  /** Playback links expire: asks for a new one to continue where it stopped. */
  getFreshUrl: () => Promise<string | null>;
};

const iconButton =
  "press grid size-9 shrink-0 place-items-center rounded-md text-bone hover:bg-white/15";

export function VideoPlayer({ url, poster, title, getFreshUrl }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const hlsRef = useRef<Hls | null>(null);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const resume = useRef<{ time: number; playing: boolean } | null>(null);
  const recoveries = useRef(0);
  const failedAt = useRef(0);
  const freshUrl = useRef(getFreshUrl);

  // An object, so asking for the same URL again still reloads the player.
  const [source, setSource] = useState({ url });
  const [playing, setPlaying] = useState(false);
  const [started, setStarted] = useState(false);
  const [waiting, setWaiting] = useState(true);
  const [time, setTime] = useState(0);
  const [duration, setDuration] = useState(0);
  const [buffered, setBuffered] = useState(0);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [levels, setLevels] = useState<QualityLevel[]>([]);
  const [activeLevel, setActiveLevel] = useState(AUTO_LEVEL);
  const [selectedLevel, setSelectedLevel] = useState(AUTO_LEVEL);
  const [fullscreen, setFullscreen] = useState(false);
  const [controlsVisible, setControlsVisible] = useState(true);
  const [failure, setFailure] = useState<string | null>(null);

  useEffect(() => {
    freshUrl.current = getFreshUrl;
  }, [getFreshUrl]);

  const recover = useCallback(async () => {
    const video = videoRef.current;
    if (!video) return;
    if (recoveries.current >= MAX_RECOVERIES) {
      setFailure("No se pudo cargar el video.");
      return;
    }
    recoveries.current += 1;
    failedAt.current = video.currentTime;
    resume.current = { time: video.currentTime, playing: !video.paused };
    const next = await freshUrl.current().catch(() => null);
    if (next) setSource({ url: next });
    else setFailure("No se pudo cargar el video.");
  }, []);

  useEffect(() => {
    const video = videoRef.current;
    if (!video) return;
    let disposed = false;
    let instance: Hls | null = null;
    let mediaRecovered = false;
    let expired = false;

    void import("hls.js").then(({ default: HlsPlayer }) => {
      if (disposed) return;
      if (!HlsPlayer.isSupported()) {
        if (video.canPlayType("application/vnd.apple.mpegurl")) video.src = source.url;
        else setFailure("Tu navegador no puede reproducir este video.");
        return;
      }
      const hls = new HlsPlayer({ capLevelToPlayerSize: true });
      instance = hls;
      hlsRef.current = hls;
      hls.on(HlsPlayer.Events.MANIFEST_PARSED, (_event, data) => {
        setLevels(data.levels.map(({ height }, index) => ({ index, height })));
      });
      hls.on(HlsPlayer.Events.LEVEL_SWITCHED, (_event, data) => setActiveLevel(data.level));
      hls.on(HlsPlayer.Events.ERROR, (_event, data) => {
        // Every link of a video expires together, so trying other qualities is pointless.
        const status = data.response?.code;
        if (status === 401 || status === 403) {
          if (!expired) {
            expired = true;
            hls.stopLoad();
            void recover();
          }
          return;
        }
        if (!data.fatal) return;
        if (data.type === HlsPlayer.ErrorTypes.MEDIA_ERROR && !mediaRecovered) {
          mediaRecovered = true;
          hls.recoverMediaError();
          return;
        }
        void recover();
      });
      hls.loadSource(source.url);
      hls.attachMedia(video);
    });

    return () => {
      disposed = true;
      instance?.destroy();
      hlsRef.current = null;
    };
  }, [source, recover]);

  useEffect(() => {
    const onChange = () => setFullscreen(document.fullscreenElement === container.current);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  useEffect(() => () => clearTimeout(hideTimer.current), []);

  // House lights: the rest of the app goes dark while the video plays.
  useEffect(() => {
    const root = document.documentElement;
    if (playing) root.dataset.lights = "down";
    else delete root.dataset.lights;
    return () => {
      delete root.dataset.lights;
    };
  }, [playing]);

  const revealControls = useCallback(() => {
    setControlsVisible(true);
    clearTimeout(hideTimer.current);
    hideTimer.current = setTimeout(() => {
      if (!videoRef.current?.paused) setControlsVisible(false);
    }, HIDE_CONTROLS_MS);
  }, []);

  const togglePlay = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    if (video.paused) void video.play().catch(() => undefined);
    else video.pause();
  }, []);

  const seekTo = useCallback((seconds: number) => {
    const video = videoRef.current;
    if (!video || !Number.isFinite(video.duration)) return;
    video.currentTime = Math.min(video.duration, Math.max(0, seconds));
    setTime(video.currentTime);
  }, []);

  const changeVolume = useCallback((next: number) => {
    const video = videoRef.current;
    if (!video) return;
    video.volume = Math.min(1, Math.max(0, next));
    video.muted = video.volume === 0;
  }, []);

  const toggleMute = useCallback(() => {
    const video = videoRef.current;
    if (!video) return;
    video.muted = !video.muted;
    if (!video.muted && video.volume === 0) video.volume = 0.5;
  }, []);

  const toggleFullscreen = useCallback(() => {
    const element = container.current;
    const video = videoRef.current as
      | (HTMLVideoElement & { webkitEnterFullscreen?: () => void })
      | null;
    if (document.fullscreenElement) void document.exitFullscreen();
    else if (element?.requestFullscreen) void element.requestFullscreen();
    else video?.webkitEnterFullscreen?.();
  }, []);

  const chooseLevel = (level: number) => {
    setSelectedLevel(level);
    if (hlsRef.current) hlsRef.current.currentLevel = level;
  };

  const retry = () => {
    recoveries.current = 0;
    setFailure(null);
    void recover();
  };

  const onKeyDown = (event: KeyboardEvent) => {
    const target = event.target as HTMLElement;
    if (target.tagName === "SELECT" || event.altKey || event.ctrlKey || event.metaKey) return;
    const onSlider = target.tagName === "INPUT";
    const video = videoRef.current;
    if (!video) return;
    const actions: Record<string, () => void> = {
      k: togglePlay,
      m: toggleMute,
      f: toggleFullscreen,
    };
    if (!onSlider) {
      actions.ArrowLeft = () => seekTo(video.currentTime - SEEK_SECONDS);
      actions.ArrowRight = () => seekTo(video.currentTime + SEEK_SECONDS);
      actions.ArrowUp = () => changeVolume(video.volume + VOLUME_STEP);
      actions.ArrowDown = () => changeVolume(video.volume - VOLUME_STEP);
    }
    if (target.tagName !== "BUTTON") actions[" "] = togglePlay;
    const action = actions[event.key];
    if (action) {
      event.preventDefault();
      action();
      revealControls();
    }
  };

  const played = duration > 0 ? (time / duration) * 100 : 0;
  const loadedPercent = duration > 0 ? (buffered / duration) * 100 : 0;
  const showControls = controlsVisible || !playing;

  return (
    <div
      ref={container}
      role="region"
      aria-label={`Reproductor: ${title}`}
      tabIndex={0}
      onKeyDown={onKeyDown}
      onMouseMove={revealControls}
      onMouseLeave={() => playing && setControlsVisible(false)}
      onFocus={revealControls}
      data-controls={showControls}
      className={`player group relative aspect-video max-h-[calc(100dvh-var(--player-offset,0px))] w-full overflow-hidden bg-black -outline-offset-2 ${
        showControls ? "" : "cursor-none"
      }`}
    >
      <video
        ref={videoRef}
        poster={poster ?? undefined}
        playsInline
        preload="metadata"
        onClick={togglePlay}
        onDoubleClick={toggleFullscreen}
        onPlay={() => {
          setPlaying(true);
          setStarted(true);
          revealControls();
        }}
        onPause={() => {
          setPlaying(false);
          setControlsVisible(true);
        }}
        onPlaying={() => setWaiting(false)}
        onWaiting={() => setWaiting(true)}
        onCanPlay={() => setWaiting(false)}
        onTimeUpdate={(event) => {
          const now = event.currentTarget.currentTime;
          setTime(now);
          // Only real progress clears the count, or a bad segment would retry forever.
          if (now > failedAt.current + RECOVERED_AFTER_SECONDS) recoveries.current = 0;
        }}
        onDurationChange={(event) => setDuration(event.currentTarget.duration || 0)}
        onLoadedMetadata={(event) => {
          const position = resume.current;
          if (!position) return;
          resume.current = null;
          event.currentTarget.currentTime = position.time;
          if (position.playing) void event.currentTarget.play().catch(() => undefined);
        }}
        onProgress={(event) => {
          const ranges = event.currentTarget.buffered;
          const now = event.currentTarget.currentTime;
          for (let index = 0; index < ranges.length; index += 1) {
            if (ranges.start(index) <= now && now <= ranges.end(index)) {
              setBuffered(ranges.end(index));
              break;
            }
          }
        }}
        onVolumeChange={(event) => {
          setVolume(event.currentTarget.volume);
          setMuted(event.currentTarget.muted);
        }}
        onError={() => {
          if (!hlsRef.current) void recover();
        }}
        className="size-full"
      />

      {failure ? (
        <div role="alert" className="absolute inset-0 grid place-items-center bg-black/80 p-6 text-center">
          <div>
            <p className="font-medium">{failure}</p>
            <Button className="mt-3" onClick={retry}>
              <RotateCcw aria-hidden className="size-4" />
              Reintentar
            </Button>
          </div>
        </div>
      ) : (
        <>
          {waiting && started && (
            <div role="status" aria-label="Cargando video" className="pointer-events-none absolute inset-0 grid place-items-center">
              <LoaderCircle aria-hidden className="text-brass size-9 animate-spin" />
            </div>
          )}
          {!started && (
            <button
              type="button"
              aria-label="Reproducir"
              onClick={togglePlay}
              className="group/start absolute inset-0 grid cursor-pointer place-items-center bg-black/25"
            >
              <span className="press bg-brass text-brass-ink shadow-float group-hover/start:bg-brass-bright grid size-16 place-items-center rounded-full motion-safe:group-active/start:[transform:scale(0.97)]">
                <Play aria-hidden className="ml-0.5 size-7 fill-current" />
              </span>
            </button>
          )}
          <div className={`player-controls absolute inset-x-0 bottom-0 px-3 pt-10 pb-2 ${started ? "" : "invisible"}`}>
            <div className="player-timeline relative h-5">
              <span
                ref={fitDots}
                aria-hidden
                className="player-track"
                style={{ "--played": `${played}%`, "--buffered": `${loadedPercent}%` } as CSSProperties}
              />
              <input
                type="range"
                aria-label="Posición del video"
                aria-valuetext={`${formatDuration(time)} de ${formatDuration(duration)}`}
                min={0}
                max={duration || 0}
                step={0.1}
                value={Math.min(time, duration || 0)}
                onChange={(event) => seekTo(Number(event.target.value))}
                className="player-range absolute inset-0 w-full"
              />
            </div>
            <div className="mt-0.5 flex items-center gap-1">
              <button type="button" className={iconButton} aria-label={playing ? "Pausar" : "Reproducir"} onClick={togglePlay}>
                {playing ? <Pause aria-hidden className="size-[18px] fill-current" /> : <Play aria-hidden className="size-[18px] fill-current" />}
              </button>
              <button type="button" className={iconButton} aria-label={muted ? "Activar sonido" : "Silenciar"} onClick={toggleMute}>
                {muted || volume === 0 ? <VolumeX aria-hidden className="size-[18px]" /> : <Volume2 aria-hidden className="size-[18px]" />}
              </button>
              <input
                type="range"
                aria-label="Volumen"
                min={0}
                max={1}
                step={0.05}
                value={muted ? 0 : volume}
                onChange={(event) => changeVolume(Number(event.target.value))}
                style={{ "--value": `${(muted ? 0 : volume) * 100}%` } as CSSProperties}
                className="player-volume hidden w-20 sm:block"
              />
              <span className="tabular text-bone ml-2 text-[13px] whitespace-nowrap">
                {formatDuration(time)} / {formatDuration(duration)}
              </span>
              <span className="flex-1" />
              {levels.length > 1 && (
                <select
                  aria-label="Calidad"
                  value={selectedLevel}
                  onChange={(event) => chooseLevel(Number(event.target.value))}
                  className="player-select h-8 cursor-pointer rounded-md border border-white/25 bg-black/50 px-2 text-[13px] text-bone"
                >
                  {qualityOptions(levels, activeLevel, selectedLevel).map(({ value, label }) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
              )}
              <button
                type="button"
                className={iconButton}
                aria-label={fullscreen ? "Salir de pantalla completa" : "Pantalla completa"}
                onClick={toggleFullscreen}
              >
                {fullscreen ? <Minimize aria-hidden className="size-[18px]" /> : <Maximize aria-hidden className="size-[18px]" />}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
