import { CircleAlert, CircleCheck, Clock3, LoaderCircle, UploadCloud } from "lucide-react";

import type { Video, VideoStatus } from "./api";

const STATES: Record<
  VideoStatus,
  { label: string; icon: typeof Clock3; color: string; spin?: boolean }
> = {
  PENDING_UPLOAD: { label: "Sin terminar", icon: UploadCloud, color: "text-muted" },
  UPLOADED: { label: "En cola", icon: Clock3, color: "text-bone-soft" },
  PROCESSING: {
    label: "Procesando",
    icon: LoaderCircle,
    color: "text-brass-bright",
    spin: true,
  },
  READY: { label: "Listo", icon: CircleCheck, color: "text-ok" },
  FAILED: { label: "Falló", icon: CircleAlert, color: "text-danger" },
};

type Props = Pick<Video, "status"> & { progress?: number };

export function StatusMark({ status, progress }: Props) {
  const { label, icon: Icon, color, spin } = STATES[status];
  return (
    <span className={`inline-flex items-center gap-1.5 font-medium whitespace-nowrap ${color}`}>
      <Icon aria-hidden className={`size-3.5 ${spin ? "animate-spin" : ""}`} />
      {label}
      {status === "PROCESSING" && progress !== undefined && (
        <span className="tabular text-bone">{progress} %</span>
      )}
    </span>
  );
}
