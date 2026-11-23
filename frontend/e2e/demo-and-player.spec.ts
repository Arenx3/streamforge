import { enterDemo, expect, playerState, SAMPLE, test, videoRow, waitForReady } from "./helpers";

test.describe("demo and player", () => {
  test.beforeEach(async ({ page }) => {
    await enterDemo(page);
    await waitForReady(page, SAMPLE);
    await videoRow(page, SAMPLE).getByRole("link").first().click();
    await expect(page.getByRole("region", { name: /Reproductor/ })).toBeVisible();
  });

  test("plays the processed sample and switches quality", async ({ page }) => {
    await page.getByRole("button", { name: "Reproducir" }).first().click();
    await expect.poll(async () => (await playerState(page)).time).toBeGreaterThan(0.5);

    const quality = page.getByRole("combobox", { name: "Calidad" });
    await expect(quality.locator("option")).toHaveText([/^Auto/, "1080p", "720p", "360p"]);

    await quality.selectOption({ label: "360p" });
    await expect.poll(async () => (await playerState(page)).width).toBe(640);

    await quality.selectOption({ label: "1080p" });
    await expect.poll(async () => (await playerState(page)).width).toBe(1920);
  });

  test("works from the keyboard", async ({ page }) => {
    await page.getByRole("button", { name: "Reproducir" }).first().click();
    await expect.poll(async () => (await playerState(page)).paused).toBe(false);
    await page.getByRole("region", { name: /Reproductor/ }).focus();

    await page.keyboard.press("k");
    await expect.poll(async () => (await playerState(page)).paused).toBe(true);

    const before = (await playerState(page)).time;
    await page.keyboard.press("ArrowRight");
    await expect.poll(async () => (await playerState(page)).time).toBeGreaterThan(before + 4);

    await page.keyboard.press("m");
    await expect.poll(async () => (await playerState(page)).muted).toBe(true);
  });

  test("renews an expired link without losing its place", async ({ page, browserErrors }) => {
    let expired = 0;
    await page.route("**/seg_00001.ts*", async (route) => {
      if (expired === 0) {
        expired += 1;
        await route.fulfill({ status: 403, body: "expired" });
      } else {
        await route.continue();
      }
    });
    await page.reload();
    await page.getByRole("button", { name: "Reproducir" }).first().click();

    await expect.poll(async () => (await playerState(page)).time, { timeout: 40_000 }).toBeGreaterThan(7);

    expect(expired).toBe(1);
    await expect(page.getByRole("alert")).toHaveCount(0);
    browserErrors.length = 0; // The 403 above is logged by the browser on purpose.
  });
});
