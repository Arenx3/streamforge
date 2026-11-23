import { emailLink, enterDemo, expect, PASSWORD, test, unique } from "./helpers";

const NEW_PASSWORD = "telon-de-terciopelo-77";

async function register(page: import("@playwright/test").Page) {
  const email = `${unique("cuenta")}@example.com`;
  await page.goto("/register");
  await page.getByLabel("Correo").fill(email);
  await page.getByLabel("Contraseña").fill(PASSWORD);
  await page.getByRole("button", { name: "Crear cuenta" }).click();
  await expect(page.getByRole("heading", { name: "Revisa tu correo" })).toBeVisible();
  return email;
}

async function logIn(page: import("@playwright/test").Page, email: string, password: string) {
  await page.getByLabel("Correo").fill(email);
  await page.getByLabel("Contraseña", { exact: true }).fill(password);
  await page.getByRole("button", { name: "Iniciar sesión" }).click();
}

test("registers and confirms the email from the inbox", async ({ page, request }) => {
  const email = await register(page);

  await page.goto(await emailLink(request, email, "Confirma"));
  await expect(page.getByText("Inicia sesión para confirmar tu correo.")).toBeVisible();
  await logIn(page, email, PASSWORD);
  await expect(page.getByRole("heading", { name: "Correo confirmado" })).toBeVisible();

  await page.getByRole("link", { name: "Ir a la biblioteca" }).click();
  await expect(page.getByText("La cartelera está vacía")).toBeVisible();
  await expect(page.getByText("Confirma tu correo para subir videos.")).toHaveCount(0);
});

test("an unverified account can enter but not upload", async ({ page }) => {
  const email = await register(page);
  await page.goto("/login");
  await logIn(page, email, PASSWORD);

  await expect(page.getByText("Confirma tu correo para subir videos.")).toBeVisible();
  await page.getByRole("link", { name: "Subir video" }).first().click();
  await expect(page.getByRole("button", { name: "Elegir archivos" })).toBeDisabled();
});

test("recovers a forgotten password and closes old access", async ({
  page,
  request,
  browserErrors,
}) => {
  const email = await register(page);

  await page.goto("/forgot-password");
  await page.getByLabel("Correo").fill(email);
  await page.getByRole("button", { name: "Enviar enlace" }).click();
  await expect(page.getByText("recibirás un enlace válido")).toBeVisible();

  await page.goto(await emailLink(request, email, "Restablece"));
  await page.getByLabel("Contraseña nueva").fill(NEW_PASSWORD);
  await page.getByLabel("Repite la contraseña").fill(NEW_PASSWORD);
  await page.getByRole("button", { name: "Guardar contraseña" }).click();
  await expect(page.getByText("Contraseña actualizada")).toBeVisible();

  await logIn(page, email, PASSWORD);
  await expect(page.getByText("Correo o contraseña incorrectos.")).toBeVisible();
  browserErrors.length = 0; // The rejected login is logged by the browser on purpose.

  await logIn(page, email, NEW_PASSWORD);
  await expect(page.getByRole("heading", { name: "Biblioteca" })).toBeVisible();
  await expect(page.getByText("Confirma tu correo para subir videos.")).toHaveCount(0);
});

test("keeps the session after a reload and ends it on logout", async ({ page }) => {
  await enterDemo(page);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Biblioteca" })).toBeVisible();

  await page.getByRole("button", { name: "Cerrar sesión" }).click();
  await expect(page).toHaveURL(/\/login$/);

  await page.goto("/upload");
  await expect(page).toHaveURL(/\/login$/);
});
