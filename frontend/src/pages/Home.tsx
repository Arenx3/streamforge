import { PageHeader } from "../components/layout/AppShell";
import { Perforation } from "../components/ui/Perforation";

export function Home() {
  return (
    <>
      <PageHeader title="Biblioteca" summary="Tus videos aparecerán aquí." />
      <Perforation value={0} label="Sin videos" tone="idle" className="max-w-xs" />
    </>
  );
}
