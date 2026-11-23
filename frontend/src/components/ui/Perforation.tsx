import type { CSSProperties } from "react";

export type PerforationTone = "live" | "done" | "failed" | "idle";

type Props = {
  value: number;
  label: string;
  tone?: PerforationTone;
  /** Says what the number means when it is not obvious. */
  valueText?: string;
  className?: string;
};

const DOT = 6;
const MIN_PITCH = 9;

/** The spacing that fits a whole number of dots, with one flush against each end. */
export function fitPitch(width: number) {
  const gaps = Math.floor((width - DOT) / MIN_PITCH);
  return gaps > 0 ? (width - DOT) / gaps : MIN_PITCH;
}

/** Ref for a dotted line: keeps its dots whole and flush with both of its ends. */
export function fitDots(element: HTMLElement) {
  const observer = new ResizeObserver(([entry]) => {
    if (entry) element.style.setProperty("--pitch", `${fitPitch(entry.contentRect.width)}px`);
  });
  observer.observe(element);
  return () => observer.disconnect();
}

/** The punched line with nothing to report, for a ticket that is only decoration. */
export function PerforationRule() {
  return <span ref={fitDots} className="perforation" data-tone="idle" />;
}

/** The product's progress meter: a ticket's punched line filling with real progress. */
export function Perforation({ value, label, tone = "live", valueText, className = "" }: Props) {
  const percent = Math.min(100, Math.max(0, Math.round(value)));

  return (
    <span
      ref={fitDots}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percent}
      aria-valuetext={valueText}
      data-tone={tone}
      className={`perforation ${className}`}
      style={{ "--progress": `${percent}%` } as CSSProperties}
    />
  );
}
