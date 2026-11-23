import { LoaderCircle } from "lucide-react";
import { Navigate, Outlet, useLocation, type Location } from "react-router";

import { Button } from "../../components/ui/Button";
import { useAuth } from "./AuthProvider";

export type AuthRouteState = { from?: Location; notice?: string } | null;

export function FullPageLoader() {
  return (
    <div role="status" aria-label="Cargando" className="grid min-h-dvh place-items-center">
      <LoaderCircle aria-hidden className="text-brass size-6 animate-spin" />
    </div>
  );
}

export function ServiceUnavailable({ onRetry }: { onRetry: () => void }) {
  return (
    <main className="grid min-h-dvh place-items-center px-6 text-center">
      <div>
        <h1 className="marquee text-[44px] leading-none">No hay conexión</h1>
        <p className="text-muted mt-2">El servidor no responde. Revisa que Docker siga corriendo.</p>
        <Button className="mt-5" onClick={onRetry}>
          Reintentar
        </Button>
      </div>
    </main>
  );
}

export function RequireAuth() {
  const { status, retry } = useAuth();
  const location = useLocation();
  if (status === "loading") return <FullPageLoader />;
  if (status === "unavailable") return <ServiceUnavailable onRetry={retry} />;
  if (status === "anonymous") {
    return <Navigate to="/login" replace state={{ from: location } satisfies AuthRouteState} />;
  }
  return <Outlet />;
}

export function PublicOnly() {
  const { status } = useAuth();
  const location = useLocation();
  if (status === "loading") return <FullPageLoader />;
  if (status === "authenticated") {
    const from = (location.state as AuthRouteState)?.from;
    return <Navigate to={from ? `${from.pathname}${from.search}${from.hash}` : "/"} replace />;
  }
  return <Outlet />;
}
