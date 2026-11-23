import { useMutation } from "@tanstack/react-query";
import { LogOut } from "lucide-react";
import { toast } from "sonner";

import { Button } from "../../components/ui/Button";
import { Notice } from "../../components/ui/Notice";
import { api } from "../../lib/api";
import { errorMessage } from "../../lib/errors";
import { MailboxLink } from "./AuthLayout";
import { useAuth } from "./AuthProvider";

export function AccountMenu() {
  const { user, logout } = useAuth();
  return (
    <div className="flex items-center gap-1 md:px-1 md:group-data-[compact=true]/rail:justify-center md:group-data-[compact=true]/rail:px-0">
      <span title={user?.email} className="text-rail-text hidden min-w-0 flex-1 truncate text-xs md:block md:group-data-[compact=true]/rail:hidden">
        {user?.email}
      </span>
      <Button
        variant="ghost"
        size="icon"
        aria-label="Cerrar sesión"
        title="Cerrar sesión"
        className="text-rail-text hover:bg-rail-deep/60"
        onClick={() => void logout()}
      >
        <LogOut aria-hidden className="size-4" />
      </Button>
    </div>
  );
}

export function UnverifiedBanner() {
  const { user } = useAuth();
  const resend = useMutation({
    mutationFn: () => api("/auth/email/resend/", { method: "POST" }),
    onSuccess: () => toast.success("Correo enviado"),
    onError: (error) => toast.error(errorMessage(error)),
  });
  if (!user || user.email_verified) return null;
  return (
    <Notice tone="info" className="mb-6">
      <span className="font-medium">Confirma tu correo para subir videos.</span> Enviamos un enlace
      a {user.email}.
      <span className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1">
        <button
          type="button"
          disabled={resend.isPending}
          onClick={() => resend.mutate()}
          className="text-bone font-medium underline disabled:opacity-60"
        >
          {resend.isPending ? "Enviando…" : "Reenviar correo"}
        </button>
        <MailboxLink />
      </span>
    </Notice>
  );
}
