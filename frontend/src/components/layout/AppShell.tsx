import { Film, UploadCloud } from "lucide-react";
import type { ReactNode } from "react";
import { Link, NavLink } from "react-router";

const NAV = [
  { to: "/", label: "Biblioteca", icon: Film, end: true },
  { to: "/upload", label: "Subir video", icon: UploadCloud, end: false },
];

const navClass = ({ isActive }: { isActive: boolean }) =>
  `press flex h-9 items-center gap-2.5 rounded-md px-2.5 font-medium ${
    isActive ? "bg-rail-deep text-bone" : "text-rail-text hover:bg-rail-deep/60 hover:text-bone"
  }`;

export function AppShell({ account, children }: { account?: ReactNode; children: ReactNode }) {
  return (
    <div className="min-h-dvh md:grid md:grid-cols-[232px_minmax(0,1fr)]">
      <a
        href="#contenido"
        className="bg-brass text-brass-ink fixed top-2 left-2 z-50 -translate-y-16 rounded-md px-3 py-2 font-semibold focus:translate-y-0"
      >
        Saltar al contenido
      </a>
      <aside className="bg-rail border-rail-line sticky top-0 z-30 flex border-b px-3.5 py-2.5 md:h-dvh md:flex-col md:border-r md:border-b-0 md:py-5">
        <Link
          to="/"
          className="marquee text-marquee flex items-center px-2.5 text-[28px] leading-8 md:pb-5 md:text-[34px] md:leading-9"
        >
          StreamForge
        </Link>
        <nav aria-label="Principal" className="ml-auto flex gap-0.5 md:ml-0 md:flex-1 md:flex-col">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} className={navClass} aria-label={label}>
              <Icon aria-hidden className="size-4" />
              <span className="hidden md:inline">{label}</span>
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
