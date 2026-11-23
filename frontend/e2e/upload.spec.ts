import { randomBytes } from "node:crypto";

import {
  chooseFile,
  enterDemo,
  expect,
  playerState,
  sampleVideo,
  stateIn,
  test,
  unique,
  uploadSample,
  videoRow,
  waitForReady,
} from "./helpers";

test.beforeEach(async ({ page }) => {
  await enterDemo(page);
});

test("uploads, processes live and plays a video", async ({ page }) => {
  const title = unique("subida");
  await uploadSample(page, title);

  await page.getByRole("link", { name: "Biblioteca" }).first().click();
  await expect(videoRow(page, title)).toBeVisible();
  await waitForReady(page, title);
  await expect(videoRow(page, title).locator("img")).toBeVisible();

  await videoRow(page, title).getByRole("link").first().click();
  await page.getByRole("button", { name: "Reproducir" }).first().click();
  await expect.poll(async () => (await playerState(page)).time).toBeGreaterThan(0.3);
});

test("receives progress without polling the list", async ({ page }) => {
  const title = unique("en-vivo");
  await uploadSample(page, title);
  await page.getByRole("link", { name: "Biblioteca" }).first().click();
  await expect(videoRow(page, title)).toBeVisible();

  let listRequests = 0;
  page.on("request", (request) => {
    if (/\/api\/videos\/\?/.test(request.url())) listRequests += 1;
  });
  await waitForReady(page, title);

  // One refetch per state change at most, however long processing takes.
  expect(listRequests).toBeLessThanOrEqual(3);
});

test("cancels an upload and leaves nothing behind", async ({ page }) => {
  const title = unique("cancelada");
  let release = () => {};
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/*uploadId=*", async (route) => {
    await held;
    await route.abort().catch(() => undefined);
  });

  await page.getByRole("link", { name: "Subir video" }).first().click();
  await chooseFile(page, `${title}.mp4`, sampleVideo());
  await page.getByRole("button", { name: `Cancelar ${title}` }).click();
  await expect(page.getByText(title)).toHaveCount(0);
  release();
  await page.unrouteAll({ behavior: "ignoreErrors" });

  await page.getByRole("link", { name: "Biblioteca" }).first().click();
  await expect(page.getByRole("heading", { name: "Biblioteca" })).toBeVisible();
  await expect.poll(() => videoRow(page, title).count()).toBe(0);
});

test("explains a corrupt file", async ({ page }) => {
  const title = unique("corrupto");
  await page.getByRole("link", { name: "Subir video" }).first().click();
  await chooseFile(page, `${title}.mp4`, randomBytes(4096));
  await expect(page.getByRole("link", { name: "Ver video" }).first()).toBeVisible();

  await page.getByRole("link", { name: "Biblioteca" }).first().click();
  await expect(stateIn(page, title, "Falló")).toBeVisible({ timeout: 60_000 });

  await videoRow(page, title).getByRole("link").first().click();
  await expect(page.getByText("El archivo no es un video compatible.")).toBeVisible();
});

test("refuses a file that is not a video", async ({ page }) => {
  await page.getByRole("link", { name: "Subir video" }).first().click();

  await chooseFile(page, "notas.txt", Buffer.from("no soy un video"), "text/plain");

  await expect(page.getByText("notas.txt no es un video.")).toBeVisible();
});

test("renames and deletes a video", async ({ page }) => {
  const title = unique("editar");
  const renamed = `${title}-nuevo`;
  await uploadSample(page, title);
  await page.getByRole("link", { name: "Biblioteca" }).first().click();
  await waitForReady(page, title);

  await videoRow(page, title).hover();
  await page.getByRole("button", { name: `Editar ${title}` }).click();
  await page.getByRole("textbox", { name: "Título" }).fill(renamed);
  await page.getByRole("button", { name: "Guardar" }).click();
  await expect(videoRow(page, renamed)).toBeVisible();

  await videoRow(page, renamed).hover();
  await page.getByRole("button", { name: `Borrar ${renamed}` }).click();
  await page.getByRole("button", { name: "Borrar video" }).click();
  await expect(videoRow(page, renamed)).toHaveCount(0);
});
