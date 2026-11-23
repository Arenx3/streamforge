import { Film, UploadCloud } from "lucide-react";
import type { CSSProperties, ReactNode } from "react";
import { Link, NavLink } from "react-router";

const NAV = [
  { to: "/", label: "Biblioteca", icon: Film, end: true },
  { to: "/upload", label: "Subir video", icon: UploadCloud, end: false },
];

const navClass =
  (compact: boolean) =>
  ({ isActive }: { isActive: boolean }) =>
    `press flex h-9 items-center gap-2.5 rounded-md px-2.5 font-medium ${
      compact ? "md:justify-center md:px-0" : ""
    } ${isActive ? "bg-rail-deep text-bone" : "text-rail-text hover:bg-rail-deep/60 hover:text-bone"}`;

type ShellProps = { account?: ReactNode; compact?: boolean; children: ReactNode };

export function AppShell({ account, compact = false, children }: ShellProps) {
  return (
    <div
      className="min-h-dvh md:grid md:grid-cols-[var(--rail)_minmax(0,1fr)]"
      style={{ "--rail": compact ? "64px" : "232px" } as CSSProperties}
    >
      <a
        href="#contenido"
        className="bg-brass text-brass-ink fixed top-2 left-2 z-50 -translate-y-16 rounded-md px-3 py-2 font-semibold focus:translate-y-0"
      >
        Saltar al contenido
      </a>
      <aside
        data-compact={compact}
        className={`app-rail group/rail bg-rail border-rail-line sticky top-0 z-30 flex border-b px-3.5 py-2.5 md:h-dvh md:flex-col md:border-r md:border-b-0 md:py-5 ${
          compact ? "md:px-2" : ""
        }`}
      >
        <Link
          to="/"
          aria-label="StreamForge"
          className={`marquee text-marquee flex items-center px-2.5 text-[28px] leading-8 md:pb-5 md:text-[34px] md:leading-9 ${
            compact ? "md:justify-center md:px-0" : ""
          }`}
        >
          <span className={compact ? "md:hidden" : ""}>StreamForge</span>
          {compact && (
            <span aria-hidden className="hidden md:inline">
              SF
            </span>
          )}
        </Link>
        <nav aria-label="Principal" className="ml-auto flex gap-0.5 md:ml-0 md:flex-1 md:flex-col">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={navClass(compact)}
              aria-label={label}
              title={compact ? label : undefined}
            >
              <Icon aria-hidden className="size-4" />
              <span className={compact ? "hidden" : "hidden md:inline"}>{label}</span>
            </NavLink>
          ))}
        </nav>
        {account && <div className="ml-1 md:ml-0">{account}</div>}
      </aside>
      <main id="contenido" className="min-w-0 px-4 py-6 md:px-9 md:py-8">
        {children}
      </main>
    </div>
  );
}

type HeaderProps = { title: string; summary?: ReactNode; action?: ReactNode };

export function PageHeader({ title, summary, action }: HeaderProps) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-x-6 gap-y-3">
      <div className="min-w-0">
        <h1 className="marquee text-[44px] leading-[44px] text-balance md:text-[58px] md:leading-[56px]">
          {title}
        </h1>
        {summary && <p className="text-muted mt-1 text-[13px]">{summary}</p>}
      </div>
      {action}
    </header>
  );
}
