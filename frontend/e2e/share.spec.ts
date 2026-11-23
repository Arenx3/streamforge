import { enterDemo, expect, playerState, SAMPLE, test, videoRow, waitForReady } from "./helpers";

test("shares a video with someone who has no account", async ({ page, browser, baseURL }) => {
  await enterDemo(page);
  await waitForReady(page, SAMPLE);
  await videoRow(page, SAMPLE).getByRole("link").first().click();

  await page.getByRole("button", { name: "Compartir" }).click();
  await page.getByRole("button", { name: "Crear enlace" }).click();
  const field = page.getByRole("textbox", { name: "Enlace público" });
  await expect(field).toHaveValue(/\/v\/[\w-]{32}$/);
  const link = await field.inputValue();

  const guest = await browser.newContext({ baseURL });
  const visitor = await guest.newPage();
  try {
    const opened = await visitor.goto(link);
    expect(opened?.headers()["x-robots-tag"]).toContain("noindex");
    await expect(visitor.getByRole("heading", { name: SAMPLE })).toBeVisible();
    await expect(visitor.getByRole("link", { name: "Biblioteca" })).toHaveCount(0);

    await visitor.getByRole("button", { name: "Reproducir" }).first().click();
    await expect.poll(async () => (await playerState(visitor)).time).toBeGreaterThan(0.5);
    await expect(visitor.getByRole("combobox", { name: "Calidad" }).locator("option")).toHaveText([
      /^Auto/,
      "1080p",
      "720p",
      "360p",
    ]);

    await page.getByRole("button", { name: "Desactivar enlace" }).click();
    await expect(page.getByRole("button", { name: "Crear enlace" })).toBeVisible();

    await visitor.reload();
    await expect(visitor.getByRole("heading", { name: "Función cancelada" })).toBeVisible();
  } finally {
    await guest.close();
  }
});
