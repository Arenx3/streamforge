import { ExternalLink } from "lucide-react";
import type { ReactNode } from "react";
import { Link } from "react-router";

import { useConfig } from "./AuthProvider";

const STAGES = [
  ["Subida", "Directa a S3, por partes"],
  ["Proceso", "FFmpeg en workers de Celery"],
  ["Función", "HLS en 360p, 720p y 1080p"],
];

function PipelineTicket() {
  return (
    <div aria-hidden className="ticket bg-ground text-bone relative max-w-[420px] rounded-xl">
      <div className="px-6 pt-5 pb-4">
        <div className="marquee text-marquee text-[26px] leading-7">Una función, tres pasos</div>
        <dl className="mt-3">
          {STAGES.map(([stage, detail]) => (
            <div key={stage} className="border-line flex justify-between gap-4 border-b border-dashed py-2 last:border-0">
              <dt className="font-medium">{stage}</dt>
              <dd className="text-muted text-right text-[13px]">{detail}</dd>
            </div>
          ))}
        </dl>
      </div>
      <div className="px-6 pb-5">
        <span className="perforation perforation-intro" />
      </div>
    </div>
  );
}

type Props = { title: string; subtitle?: ReactNode; children: ReactNode; footer?: ReactNode };

export function AuthLayout({ title, subtitle, children, footer }: Props) {
  return (
    <div className="min-h-dvh lg:grid lg:grid-cols-[minmax(0,1.05fr)_minmax(0,1fr)]">
      <aside className="bg-rail border-rail-line flex flex-col border-b px-6 py-5 lg:min-h-dvh lg:border-r lg:border-b-0 lg:px-14 lg:py-10">
        <Link to="/login" className="marquee text-marquee w-fit text-[30px] leading-8 lg:text-[38px] lg:leading-10">
          StreamForge
        </Link>
        <div className="my-auto hidden py-10 lg:block">
          <p className="marquee max-w-[11ch] text-[88px] leading-[84px] text-balance">
            Tu video, listo para transmitir
          </p>
          <p className="text-rail-text mt-5 max-w-[46ch] text-[15px] leading-6">
            Súbelo, mira cómo se procesa en vivo y reprodúcelo en calidad adaptativa. Todo corre en
            tu máquina.
          </p>
          <div className="mt-9">
            <PipelineTicket />
          </div>
        </div>
      </aside>
      <main className="flex items-center justify-center px-5 py-10 lg:px-10">
        <div className="w-full max-w-[380px]">
          <h1 className="marquee text-[44px] leading-[44px]">{title}</h1>
          {subtitle && <p className="text-muted mt-1.5">{subtitle}</p>}
          <div className="mt-6">{children}</div>
          {footer && <div className="text-muted mt-6 text-center text-[13px]">{footer}</div>}
        </div>
      </main>
    </div>
  );
}

export function MailboxLink() {
  const { data } = useConfig();
  if (!data?.mailbox_url) return null;
  return (
    <a
      href={data.mailbox_url}
      target="_blank"
      rel="noreferrer"
      className="text-brass inline-flex items-center gap-1 font-medium underline"
    >
      Abrir bandeja local
      <ExternalLink aria-hidden className="size-3.5" />
    </a>
  );
}

export const textLink = "text-bone font-medium underline hover:text-brass transition-colors";
