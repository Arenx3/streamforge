import { CircleAlert, CircleCheck, Mail } from "lucide-react";
import type { ReactNode } from "react";

const TONES = {
  success: { icon: CircleCheck, box: "border-ok/40 bg-ok/10", mark: "text-ok" },
  error: { icon: CircleAlert, box: "border-danger/50 bg-danger-deep/60", mark: "text-danger" },
  info: { icon: Mail, box: "border-brass/40 bg-brass/10", mark: "text-brass" },
};

type Props = { tone: keyof typeof TONES; children: ReactNode; className?: string };

export function Notice({ tone, children, className = "" }: Props) {
  const { icon: Icon, box, mark } = TONES[tone];
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className={`flex gap-2.5 rounded-lg border px-3 py-2.5 ${box} ${className}`}
    >
      <Icon aria-hidden className={`mt-0.5 size-4 shrink-0 ${mark}`} />
      <div className="min-w-0 text-[13px] leading-5">{children}</div>
    </div>
  );
}
