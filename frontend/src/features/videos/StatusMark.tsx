import { CircleAlert, CircleCheck, Clock3, LoaderCircle, UploadCloud } from "lucide-react";

import type { PerforationTone } from "../../components/ui/Perforation";
import type { Video, VideoStatus } from "./api";

const STATES: Record<
  VideoStatus,
  { label: string; icon: typeof Clock3; color: string; spin?: boolean; tone: PerforationTone }
> = {
  PENDING_UPLOAD: { label: "Sin terminar", icon: UploadCloud, color: "text-muted", tone: "idle" },
  UPLOADED: { label: "En cola", icon: Clock3, color: "text-brass-bright", tone: "live" },
  PROCESSING: {
    label: "Procesando",
    icon: LoaderCircle,
    color: "text-brass-bright",
    spin: true,
    tone: "live",
  },
  READY: { label: "Listo", icon: CircleCheck, color: "text-ok", tone: "done" },
  FAILED: { label: "Falló", icon: CircleAlert, color: "text-danger", tone: "failed" },
};

export const statusLabel = (status: VideoStatus) => STATES[status].label;
export const statusTone = (status: VideoStatus) => STATES[status].tone;

export function StatusMark({ status }: Pick<Video, "status">) {
  const { label, icon: Icon, color, spin } = STATES[status];
  return (
    <span className={`inline-flex items-center gap-1.5 font-medium whitespace-nowrap ${color}`}>
      <Icon aria-hidden className={`size-3.5 ${spin ? "animate-spin" : ""}`} />
      {label}
    </span>
  );
}
