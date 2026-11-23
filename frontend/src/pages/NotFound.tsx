import { Link } from "react-router";

import { buttonClass } from "../components/ui/Button";

export function NotFound() {
  return (
    <main className="grid min-h-dvh place-items-center px-6 text-center">
      <div>
        <p className="marquee text-marquee text-[120px] leading-none">404</p>
        <h1 className="mt-2 text-xl font-semibold">Esta función no está en cartelera</h1>
        <p className="text-muted mt-1">La página que buscas no existe o cambió de lugar.</p>
        <Link to="/" className={`${buttonClass("primary")} mt-6`}>
          Volver a la biblioteca
        </Link>
      </div>
    </main>
  );
}
