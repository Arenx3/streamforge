import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleCheck, Ticket } from "lucide-react";
import { useState, type FormEvent } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router";

import { Button, buttonClass } from "../../components/ui/Button";
import { Field } from "../../components/ui/Field";
import { Notice } from "../../components/ui/Notice";
import { api } from "../../lib/api";
import { errorMessage, fieldErrors } from "../../lib/errors";
import { AuthLayout, MailboxLink, textLink } from "./AuthLayout";
import { useAuth, useConfig, type User } from "./AuthProvider";
import { FullPageLoader, type AuthRouteState } from "./guards";

const tokenFromHash = (hash: string) => new URLSearchParams(hash.slice(1)).get("token") ?? "";

function formValues(event: FormEvent<HTMLFormElement>) {
  event.preventDefault();
  return Object.fromEntries(new FormData(event.currentTarget)) as Record<string, string>;
}

export function LoginPage() {
  const { login, enterDemo } = useAuth();
  const { data: config } = useConfig();
  const location = useLocation();
  const notice = (location.state as AuthRouteState)?.notice;
  const pendingLink = (location.state as AuthRouteState)?.from?.pathname === "/verify-email";
  const signIn = useMutation({
    mutationFn: ({ email, password }: Record<string, string>) => login(email!, password!),
  });
  const demo = useMutation({ mutationFn: enterDemo });
  const failed = signIn.error ?? demo.error;
  const demoEnabled = config?.demo_enabled ?? false;

  return (
    <AuthLayout
      title="Entrar"
      subtitle={demoEnabled ? "Prueba todo en un clic o usa tu cuenta." : "Usa tu cuenta."}
      footer={
        <>
          ¿No tienes cuenta?{" "}
          <Link to="/register" className={textLink}>
            Crear cuenta
          </Link>
        </>
      }
    >
      {notice && (
        <Notice tone="success" className="mb-4">
          {notice}
        </Notice>
      )}
      {pendingLink && (
        <Notice tone="info" className="mb-4">
          Inicia sesión para confirmar tu correo.
        </Notice>
      )}
      {failed && (
        <Notice tone="error" className="mb-4">
          {errorMessage(failed)}
        </Notice>
      )}
      {demoEnabled && (
        <>
          <Button className="w-full" loading={demo.isPending} onClick={() => demo.mutate()}>
            {!demo.isPending && <Ticket aria-hidden className="size-4" />}
            Entrar a la demo
          </Button>
          <div className="text-muted my-5 flex items-center gap-3 text-[13px]">
            <span className="border-line-strong flex-1 border-t border-dashed" />o con tu cuenta
            <span className="border-line-strong flex-1 border-t border-dashed" />
          </div>
        </>
      )}
      <form className="space-y-4" onSubmit={(event) => signIn.mutate(formValues(event))}>
        <Field label="Correo" name="email" type="email" autoComplete="email" required />
        <Field
          label="Contraseña"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          aside={
            <Link to="/forgot-password" className="text-muted hover:text-bone text-[13px] underline">
              ¿La olvidaste?
            </Link>
          }
        />
        <Button
          type="submit"
          variant={demoEnabled ? "secondary" : "primary"}
          className="w-full"
          loading={signIn.isPending}
        >
          Iniciar sesión
        </Button>
      </form>
    </AuthLayout>
  );
}

export function RegisterPage() {
  const register = useMutation({
    mutationFn: (body: Record<string, string>) =>
      api("/auth/register/", { method: "POST", body, auth: false }),
  });
  const errors = fieldErrors(register.error);
  const general = register.error && Object.keys(errors).length === 0;

  if (register.isSuccess) {
    return (
      <AuthLayout title="Revisa tu correo" subtitle="Te enviamos el siguiente paso.">
        <Notice tone="info">
          Abre el enlace del correo para confirmar tu cuenta. Caduca en 24 horas.
          <div className="mt-1.5">
            <MailboxLink />
          </div>
        </Notice>
        <Link to="/login" className={`${buttonClass("secondary")} mt-4 w-full`}>
          Ir a iniciar sesión
        </Link>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title="Crear cuenta"
      subtitle="Solo necesitas un correo y una contraseña."
      footer={
        <>
          ¿Ya tienes cuenta?{" "}
          <Link to="/login" className={textLink}>
            Entrar
          </Link>
        </>
      }
    >
      {general && (
        <Notice tone="error" className="mb-4">
          {errorMessage(register.error)}
        </Notice>
      )}
      <form className="space-y-4" onSubmit={(event) => register.mutate(formValues(event))}>
        <Field
          label="Correo"
          name="email"
          type="email"
          autoComplete="email"
          required
          error={errors.email}
        />
        <Field
          label="Contraseña"
          name="password"
          type="password"
          autoComplete="new-password"
          required
          minLength={8}
          error={errors.password}
          hint="Mínimo 8 caracteres."
        />
        <Button type="submit" className="w-full" loading={register.isPending}>
          Crear cuenta
        </Button>
      </form>
    </AuthLayout>
  );
}

export function ForgotPasswordPage() {
  const request = useMutation({
    mutationFn: (body: Record<string, string>) =>
      api("/auth/password/reset/", { method: "POST", body, auth: false }),
  });
  const errors = fieldErrors(request.error);

  return (
    <AuthLayout
      title="Recuperar contraseña"
      subtitle="Te enviaremos un enlace de un solo uso."
      footer={
        <Link to="/login" className={textLink}>
          Volver a entrar
        </Link>
      }
    >
      {request.isSuccess ? (
        <Notice tone="info">
          Si ese correo tiene una cuenta, recibirás un enlace válido por 1 hora.
          <div className="mt-1.5">
            <MailboxLink />
          </div>
        </Notice>
      ) : (
        <>
          {request.isError && !errors.email && (
            <Notice tone="error" className="mb-4">
              {errorMessage(request.error)}
            </Notice>
          )}
          <form className="space-y-4" onSubmit={(event) => request.mutate(formValues(event))}>
            <Field
              label="Correo"
              name="email"
              type="email"
              autoComplete="email"
              required
              error={errors.email}
            />
            <Button type="submit" className="w-full" loading={request.isPending}>
              Enviar enlace
            </Button>
          </form>
        </>
      )}
    </AuthLayout>
  );
}

export function ResetPasswordPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const [token] = useState(() => tokenFromHash(location.hash));
  const [mismatch, setMismatch] = useState(false);
  const reset = useMutation({
    mutationFn: (password: string) =>
      api("/auth/password/reset/confirm/", {
        method: "POST",
        body: { token, password },
        auth: false,
      }),
    onSuccess: () =>
      navigate("/login", {
        replace: true,
        state: { notice: "Contraseña actualizada. Ya puedes entrar." } satisfies AuthRouteState,
      }),
  });
  const errors = fieldErrors(reset.error);

  if (!token || errors.token) {
    return (
      <AuthLayout title="Enlace no válido" subtitle="Este enlace ya se usó o caducó.">
        <Link to="/forgot-password" className={`${buttonClass("primary")} w-full`}>
          Pedir otro enlace
        </Link>
      </AuthLayout>
    );
  }

  const submit = (event: FormEvent<HTMLFormElement>) => {
    const { password, confirm } = formValues(event);
    const different = password !== confirm;
    setMismatch(different);
    if (!different) reset.mutate(password!);
  };

  return (
    <AuthLayout title="Nueva contraseña" subtitle="Al guardarla se cerrarán todas tus sesiones.">
      {reset.isError && !errors.password && (
        <Notice tone="error" className="mb-4">
          {errorMessage(reset.error)}
        </Notice>
      )}
      <form className="space-y-4" onSubmit={submit}>
        <Field
          label="Contraseña nueva"
          name="password"
          type="password"
          autoComplete="new-password"
          required
          minLength={8}
          error={errors.password}
          hint="Mínimo 8 caracteres."
        />
        <Field
          label="Repite la contraseña"
          name="confirm"
          type="password"
          autoComplete="new-password"
          required
          error={mismatch ? "Las contraseñas no coinciden." : undefined}
        />
        <Button type="submit" className="w-full" loading={reset.isPending}>
          Guardar contraseña
        </Button>
      </form>
    </AuthLayout>
  );
}

export function VerifyEmailPage() {
  const { status, retry } = useAuth();
  const location = useLocation();
  const client = useQueryClient();
  const [token] = useState(() => tokenFromHash(location.hash));
  const verify = useQuery({
    queryKey: ["verify-email", token],
    queryFn: async () => {
      const user = await api<User>("/auth/email/verify/", { method: "POST", body: { token } });
      client.setQueryData(["me"], user);
      return user;
    },
    enabled: status === "authenticated" && Boolean(token),
    retry: false,
    staleTime: Infinity,
    gcTime: 0,
  });

  if (status === "loading") return <FullPageLoader />;
  if (status === "anonymous") {
    return <Navigate to="/login" replace state={{ from: location } satisfies AuthRouteState} />;
  }
  if (status === "unavailable") {
    return (
      <AuthLayout title="Sin conexión" subtitle="El servidor no responde.">
        <Button className="w-full" onClick={retry}>
          Reintentar
        </Button>
      </AuthLayout>
    );
  }
  if (token && verify.isPending) return <FullPageLoader />;
  if (verify.isSuccess) {
    return (
      <AuthLayout title="Correo confirmado" subtitle="Tu cuenta ya puede subir videos.">
        <div className="text-ok mb-4 flex items-center gap-2 font-medium">
          <CircleCheck aria-hidden className="size-5" /> Todo listo
        </div>
        <Link to="/" replace className={`${buttonClass("primary")} w-full`}>
          Ir a la biblioteca
        </Link>
      </AuthLayout>
    );
  }
  return (
    <AuthLayout title="Enlace no válido" subtitle="No corresponde a esta cuenta o ya caducó.">
      <Link to="/" replace className={`${buttonClass("primary")} w-full`}>
        Volver y pedir otro correo
      </Link>
    </AuthLayout>
  );
}
