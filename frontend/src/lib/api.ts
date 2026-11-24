/** The refresh token never reaches the page: the API keeps it in an httpOnly cookie. */
export type Tokens = { access: string };

// Only says that this browser holds a session, so tabs can tell each other when it ends.
const SESSION_KEY = "streamforge.session";
// Earlier versions kept the refresh token itself in storage.
const LEGACY_REFRESH_KEY = "streamforge.refresh";
const EXPIRY_MARGIN_MS = 20_000;

let accessToken: string | null = null;
// Without storage the session still works until the page closes.
let sessionInPage = false;
let pendingRefresh: Promise<string | null> | null = null;
const endListeners = new Set<() => void>();

export class ApiError extends Error {
  readonly status: number;
  readonly data: unknown;

  constructor(status: number, data: unknown) {
    super(`API error ${status}`);
    this.status = status;
    this.data = data;
  }
}

function readFlag() {
  try {
    return localStorage.getItem(SESSION_KEY) === "1";
  } catch {
    return false;
  }
}

function writeFlag(signedIn: boolean) {
  try {
    if (signedIn) localStorage.setItem(SESSION_KEY, "1");
    else localStorage.removeItem(SESSION_KEY);
    localStorage.removeItem(LEGACY_REFRESH_KEY);
  } catch {
    // No storage: the session lasts until the page closes.
  }
}

export const hasSession = () => accessToken !== null || sessionInPage || readFlag();

export function startSession(tokens: Tokens) {
  accessToken = tokens.access;
  sessionInPage = true;
  writeFlag(true);
}

export function endSession() {
  accessToken = null;
  sessionInPage = false;
  writeFlag(false);
  endListeners.forEach((listener) => listener());
}

export function onSessionEnd(listener: () => void) {
  endListeners.add(listener);
  const onStorage = (event: StorageEvent) => {
    if (event.key === SESSION_KEY && event.newValue === null) {
      accessToken = null;
      listener();
    }
  };
  window.addEventListener("storage", onStorage);
  return () => {
    endListeners.delete(listener);
    window.removeEventListener("storage", onStorage);
  };
}

function isFresh(token: string) {
  try {
    const payload = token.split(".")[1]!.replace(/-/g, "+").replace(/_/g, "/");
    const { exp } = JSON.parse(atob(payload)) as { exp: number };
    return exp * 1000 - Date.now() > EXPIRY_MARGIN_MS;
  } catch {
    return false;
  }
}

async function rotate(): Promise<string | null> {
  if (!hasSession()) return null;
  // The browser sends the refresh cookie, and the answer replaces it.
  const response = await fetch("/api/auth/token/refresh/", {
    method: "POST",
    credentials: "same-origin",
  });
  if (response.ok) {
    const tokens = (await response.json()) as Tokens;
    startSession(tokens);
    return tokens.access;
  }
  if (response.status === 401) {
    endSession();
    return null;
  }
  throw new ApiError(response.status, null);
}

/** Refresh tokens rotate, so tabs take turns: reusing one would end the session. */
function refreshAccess() {
  pendingRefresh ??= (
    navigator.locks ? navigator.locks.request("streamforge-refresh", rotate) : rotate()
  ).finally(() => {
    pendingRefresh = null;
  });
  return pendingRefresh;
}

export async function freshAccessToken(): Promise<string | null> {
  if (accessToken && isFresh(accessToken)) return accessToken;
  return refreshAccess();
}

type Options = {
  method?: string;
  body?: unknown;
  signal?: AbortSignal;
  /** Public endpoints never send a token. */
  auth?: boolean;
};

export async function api<T>(path: string, options: Options = {}): Promise<T> {
  const { method = "GET", body, signal, auth = true } = options;
  const send = (token: string | null) =>
    fetch(`/api${path}`, {
      method,
      signal,
      headers: {
        Accept: "application/json",
        ...(body !== undefined && { "Content-Type": "application/json" }),
        ...(token && { Authorization: `Bearer ${token}` }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });

  let response = await send(auth ? await freshAccessToken() : null);
  if (auth && response.status === 401 && hasSession()) {
    accessToken = null;
    const token = await refreshAccess();
    if (token) response = await send(token);
  }
  if (!response.ok) {
    const data: unknown = await response.json().catch(() => null);
    throw new ApiError(response.status, data);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
