import type { CSSProperties } from "react";

export type PerforationTone = "live" | "done" | "failed" | "idle";

type Props = {
  value: number;
  label: string;
  tone?: PerforationTone;
  className?: string;
};

/** The product's progress meter: a ticket's punched line filling with real progress. */
export function Perforation({ value, label, tone = "live", className = "" }: Props) {
  const percent = Math.min(100, Math.max(0, Math.round(value)));
  return (
    <span
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percent}
      data-tone={tone}
      className={`perforation ${className}`}
      style={{ "--progress": `${percent}%` } as CSSProperties}
    />
  );
}
