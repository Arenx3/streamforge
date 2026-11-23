import {
  Circle,
  CircleAlert,
  CircleCheck,
  CircleSlash,
  Clock3,
  LoaderCircle,
  UploadCloud,
} from "lucide-react";
import type { ReactNode } from "react";

import { Perforation, type PerforationTone } from "../../components/ui/Perforation";
import type { Video } from "./api";

export type StageState =
  | "waiting"
  | "unfinished"
  | "queued"
  | "active"
  | "done"
  | "failed"
  | "cancelled";
export type TicketStage = { label: string; detail: string; state: StageState };

export type TicketJourney = {
  stages: [TicketStage, TicketStage, TicketStage];
  /** The whole trip: each stage fills the third of the line right below its column. */
  percent: number;
  tone: PerforationTone;
  /** The stage in course, in words, for assistive technology. */
  spoken: string;
};

const MARKS: Record<
  StageState,
  { icon: typeof Circle; mark: string; text: string; spin?: boolean }
> = {
  waiting: { icon: Circle, mark: "text-muted", text: "text-muted" },
  unfinished: { icon: UploadCloud, mark: "text-muted", text: "text-muted" },
  queued: { icon: Clock3, mark: "text-bone-soft", text: "text-bone-soft" },
  active: { icon: LoaderCircle, mark: "text-brass-bright", text: "text-bone", spin: true },
  done: { icon: CircleCheck, mark: "text-ok", text: "text-bone-soft" },
  failed: { icon: CircleAlert, mark: "text-danger", text: "text-danger" },
  cancelled: { icon: CircleSlash, mark: "text-muted", text: "text-muted" },
};

export const STAGE_SHARE = 100 / 3;

export const ticketStage = (label: string, detail: string, state: StageState): TicketStage => ({
  label,
  detail,
  state,
});

export const pendingStage = (label: string) => ticketStage(label, "Pendiente", "waiting");

export const serialOf = (id: string) => id.replace(/-/g, "").slice(0, 6).toUpperCase();

type JourneyVideo = Pick<Video, "status" | "progress"> & { error_message?: string };

export function videoJourney({ status, progress, error_message }: JourneyVideo): TicketJourney {
  const uploaded = ticketStage("Subida", "Completa", "done");
  const processed = STAGE_SHARE * (1 + progress / 100);
  switch (status) {
    case "PENDING_UPLOAD":
      return {
        stages: [
          ticketStage("Subida", "Sin terminar", "unfinished"),
          pendingStage("Proceso"),
          pendingStage("Función"),
        ],
        percent: 0,
        tone: "idle",
        spoken: "Subida sin terminar",
      };
    case "UPLOADED":
      return {
        stages: [uploaded, ticketStage("Proceso", "En cola", "queued"), pendingStage("Función")],
        percent: STAGE_SHARE,
        tone: "done",
        spoken: "Proceso: en cola",
      };
    case "PROCESSING":
      return {
        stages: [
          uploaded,
          ticketStage("Proceso", `${progress} %`, "active"),
          pendingStage("Función"),
        ],
        percent: processed,
        tone: "live",
        spoken: `Proceso: ${progress} %`,
      };
    case "FAILED": {
      // A failed job is over: what it did not reach will never run.
      const noShow = ticketStage("Función", "Cancelada", "cancelled");
      // An abandoned upload never reached processing.
      if (error_message && /abandoned/i.test(error_message)) {
        return {
          stages: [
            ticketStage("Subida", "Falló", "failed"),
            ticketStage("Proceso", "Cancelado", "cancelled"),
            noShow,
          ],
          percent: 0,
          tone: "failed",
          spoken: "Subida: falló",
        };
      }
      return {
        stages: [uploaded, ticketStage("Proceso", "Falló", "failed"), noShow],
        percent: processed,
        tone: "failed",
        spoken: "Proceso: falló",
      };
    }
    case "READY":
      return {
        stages: [
          uploaded,
          ticketStage("Proceso", "Completo", "done"),
          ticketStage("Función", "Lista", "done"),
        ],
        percent: 100,
        tone: "done",
        spoken: "Listo para reproducir",
      };
  }
}

type Props = {
  title: string;
  serial?: string;
  journey: TicketJourney;
  heading?: "h1" | "h2";
  size?: "lg" | "md";
  /** The stub below the tear line: what is happening and what can be done. */
  children: ReactNode;
};

export function VideoTicket({
  title,
  serial,
  journey,
  heading: Heading = "h2",
  size = "md",
  children,
}: Props) {
  const large = size === "lg";
  return (
    <div className="ticket bg-raised">
      <div className={`px-5 pt-4 pb-3.5 md:px-6 ${large ? "md:pt-6 md:pb-5" : ""}`}>
        <div className="flex items-start justify-between gap-4">
          <Heading
            className={`marquee min-w-0 break-words ${
              large ? "text-[38px] leading-10 md:text-[52px] md:leading-[54px]" : "text-[26px] leading-7"
            }`}
          >
            {title}
          </Heading>
          {serial && (
            <span className="marquee text-muted tabular shrink-0 text-lg leading-7 whitespace-nowrap">
              Nº {serial}
            </span>
          )}
        </div>
        <ol className={`grid grid-cols-3 ${large ? "mt-5" : "mt-3"}`}>
          {journey.stages.map(({ label, detail, state }) => {
            const { icon: Icon, mark, text, spin } = MARKS[state];
            return (
              <li key={label} className="min-w-0 pr-2">
                <span className="text-bone flex items-center gap-1.5 text-[13px] font-medium">
                  <Icon aria-hidden className={`size-3.5 shrink-0 ${mark} ${spin ? "animate-spin" : ""}`} />
                  {label}
                </span>
                <span className={`tabular mt-0.5 block truncate text-[13px] ${text}`}>{detail}</span>
              </li>
            );
          })}
        </ol>
      </div>
      <div className="ticket-tear px-5 py-1 md:px-6">
        <Perforation
          value={journey.percent}
          tone={journey.tone}
          label={`Avance de ${title}`}
          valueText={journey.spoken}
        />
      </div>
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2.5 px-5 pt-3 pb-4 md:px-6">
        {children}
      </div>
    </div>
  );
}
