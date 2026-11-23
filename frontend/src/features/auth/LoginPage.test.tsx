import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { endSession } from "../../lib/api";
import { AuthProvider } from "./AuthProvider";
import { PublicOnly } from "./guards";
import { LoginPage } from "./pages";

const access = `h.${btoa(JSON.stringify({ exp: Math.floor(Date.now() / 1000) + 600 }))}.s`;
const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

function renderLogin() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={["/login"]}>
        <AuthProvider>
          <Routes>
            <Route element={<PublicOnly />}>
              <Route path="/login" element={<LoginPage />} />
            </Route>
            <Route path="/" element={<p>Biblioteca</p>} />
          </Routes>
        </AuthProvider>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("LoginPage", () => {
  const fetchMock = vi.fn<typeof fetch>();

  const route = (demoEnabled: boolean, login = json(200, { access, refresh: "r1" })) =>
    fetchMock.mockImplementation(async (url) => {
      if (url === "/api/config/") {
        return json(200, { demo_enabled: demoEnabled, mailbox_url: "", max_upload_bytes: 1 });
      }
      if (url === "/api/auth/me/") {
        return json(200, { id: 1, email: "demo@streamforge.local", email_verified: true });
      }
      return login.clone();
    });

  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    endSession();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("enters the demo with one click", async () => {
    route(true);
    renderLogin();

    await userEvent.click(await screen.findByRole("button", { name: "Entrar a la demo" }));

    expect(await screen.findByText("Biblioteca")).toBeInTheDocument();
    expect(fetchMock.mock.calls.map(([url]) => url)).toContain("/api/auth/demo/");
  });

  it("hides the demo when it is disabled", async () => {
    route(false);
    renderLogin();

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    await screen.findByText("Usa tu cuenta.");
    expect(screen.queryByRole("button", { name: "Entrar a la demo" })).not.toBeInTheDocument();
  });

  it("shows why a login failed", async () => {
    route(false, json(401, { detail: "No active account found with the given credentials" }));
    renderLogin();

    await userEvent.type(screen.getByLabelText("Correo"), "ana@example.com");
    await userEvent.type(screen.getByLabelText("Contraseña"), "wrong-password");
    await userEvent.click(screen.getByRole("button", { name: "Iniciar sesión" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Correo o contraseña incorrectos.");
  });
});
