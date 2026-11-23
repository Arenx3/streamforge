import { LoaderCircle } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "md" | "sm" | "icon";

const VARIANTS: Record<Variant, string> = {
  primary: "bg-brass text-brass-ink hover:bg-brass-bright font-semibold",
  secondary: "border border-line-strong bg-raised text-bone hover:bg-overlay font-medium",
  ghost: "text-bone-soft hover:bg-overlay hover:text-bone font-medium",
  danger: "bg-danger text-brass-ink hover:brightness-110 font-semibold",
};

const SIZES: Record<Size, string> = {
  md: "h-10 gap-2 rounded-[7px] px-4 text-sm",
  sm: "h-8 gap-1.5 rounded-md px-3 text-[13px]",
  icon: "size-8 rounded-md",
};

export const buttonClass = (variant: Variant = "primary", size: Size = "md") =>
  `press inline-flex shrink-0 items-center justify-center whitespace-nowrap select-none disabled:cursor-not-allowed disabled:opacity-50 ${VARIANTS[variant]} ${SIZES[size]}`;

type Props = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: Variant;
  size?: Size;
  loading?: boolean;
  children: ReactNode;
};

export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  disabled,
  className = "",
  type = "button",
  children,
  ...props
}: Props) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={`${buttonClass(variant, size)} ${className}`}
      {...props}
    >
      {loading && <LoaderCircle aria-hidden className="size-4 animate-spin" />}
      {children}
    </button>
  );
}
