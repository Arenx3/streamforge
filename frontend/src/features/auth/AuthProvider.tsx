import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import {
  api,
  endSession,
  hasSession,
  onSessionEnd,
  startSession,
  type Tokens,
} from "../../lib/api";

export type User = {
  id: number;
  email: string;
  email_verified: boolean;
  date_joined: string;
};

export type PublicConfig = {
  demo_enabled: boolean;
  mailbox_url: string;
  max_upload_bytes: number;
};

type Auth = {
  status: "loading" | "authenticated" | "anonymous" | "unavailable";
  user: User | null;
  retry: () => void;
  login: (email: string, password: string) => Promise<void>;
  enterDemo: () => Promise<void>;
  logout: () => Promise<void>;
};

const AuthContext = createContext<Auth | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const client = useQueryClient();
  const [signedIn, setSignedIn] = useState(hasSession);
  const me = useQuery({
    queryKey: ["me"],
    queryFn: () => api<User>("/auth/me/"),
    enabled: signedIn,
    retry: false,
    staleTime: 60_000,
  });

  useEffect(
    () =>
      onSessionEnd(() => {
        setSignedIn(false);
        client.clear();
      }),
    [client],
  );

  const enter = useCallback(
    async (tokens: Tokens) => {
      startSession(tokens);
      client.setQueryData(["me"], await api<User>("/auth/me/"));
      setSignedIn(true);
    },
    [client],
  );

  const login = useCallback(
    async (email: string, password: string) => {
      const body = { email, password };
      await enter(await api<Tokens>("/auth/token/", { method: "POST", body, auth: false }));
    },
    [enter],
  );

  const enterDemo = useCallback(async () => {
    await enter(await api<Tokens>("/auth/demo/", { method: "POST", auth: false }));
  }, [enter]);

  const logout = useCallback(async () => {
    endSession();
    // Revokes the refresh token the cookie carries and deletes the cookie.
    await api("/auth/logout/", { method: "POST", auth: false }).catch(() => undefined);
  }, []);

  const refetch = me.refetch;
  const retry = useCallback(() => void refetch(), [refetch]);

  const value = useMemo<Auth>(() => {
    let status: Auth["status"] = "anonymous";
    if (signedIn) {
      if (me.data) status = "authenticated";
      else status = me.isError ? "unavailable" : "loading";
    }
    return {
      status,
      user: signedIn ? (me.data ?? null) : null,
      retry,
      login,
      enterDemo,
      logout,
    };
  }, [signedIn, me.data, me.isError, retry, login, enterDemo, logout]);

  return <AuthContext value={value}>{children}</AuthContext>;
}

export function useAuth() {
  const auth = useContext(AuthContext);
  if (!auth) throw new Error("useAuth must be used inside AuthProvider");
  return auth;
}

export function useConfig() {
  return useQuery({
    queryKey: ["config"],
    queryFn: () => api<PublicConfig>("/config/", { auth: false }),
    staleTime: Infinity,
    retry: 2,
  });
}
