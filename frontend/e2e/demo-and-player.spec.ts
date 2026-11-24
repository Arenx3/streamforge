import type { Page } from "@playwright/test";

import { enterDemo, expect, playerState, SAMPLE, test, videoRow, waitForReady } from "./helpers";

/** From now on, records the longest wait between two frames shown on screen. */
const watchFrames = (page: Page) =>
  page.evaluate(() => {
    const video = document.querySelector("video")!;
    video.dataset.longestGap = "0";
    let last = 0;
    const onFrame = (now: number) => {
      if (last) video.dataset.longestGap = String(Math.max(Number(video.dataset.longestGap), now - last));
      last = now;
      video.requestVideoFrameCallback(onFrame);
    };
    video.requestVideoFrameCallback(onFrame);
  });
const longestFrameGap = (page: Page) =>
  page.evaluate(() => Number(document.querySelector("video")!.dataset.longestGap));

test.describe("demo and player", () => {
  test.beforeEach(async ({ page }) => {
    await enterDemo(page);
    await waitForReady(page, SAMPLE);
    await videoRow(page, SAMPLE).getByRole("link").first().click();
    await expect(page.getByRole("region", { name: /Reproductor/ })).toBeVisible();
  });

  // The new quality starts at the next segment, as it does on large video sites.
  for (const [label, width] of [
    ["360p", 640],
    ["1080p", 1920],
  ] as const) {
    test(`switches to ${label} without stopping`, async ({ page }) => {
      await page.getByRole("button", { name: "Reproducir" }).first().click();
      await expect.poll(async () => (await playerState(page)).time).toBeGreaterThan(0.5);
      const quality = page.getByRole("combobox", { name: "Calidad" });
      await expect(quality.locator("option")).toHaveText([/^Auto/, "1080p", "720p", "360p"]);
      await watchFrames(page);

      await quality.selectOption({ label });

      await expect.poll(async () => (await playerState(page)).width, { timeout: 15_000 }).toBe(width);
      // A frame lasts about 40 ms. Switching by emptying the buffer froze the picture for seconds.
      expect(await longestFrameGap(page)).toBeLessThan(500);
    });
  }

  test("works from the keyboard", async ({ page }) => {
    await page.getByRole("button", { name: "Reproducir" }).first().click();
    // Really playing: before the metadata loads there is no timeline to seek in.
    await expect.poll(async () => (await playerState(page)).time).toBeGreaterThan(0.3);
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

  test("keeps a play pressed while the link renews", async ({ page, browserErrors }) => {
    let expired = false;
    let finishRenewal = () => {};
    const renewal = new Promise<void>((resolve) => {
      finishRenewal = resolve;
    });
    await page.route("**/seg_00001.ts*", async (route) => {
      if (expired) return route.continue();
      expired = true;
      return route.fulfill({ status: 403, body: "expired" });
    });
    await page.route(/\/api\/videos\/[0-9a-f-]{36}\/$/, async (route) => {
      if (expired) await renewal;
      await route.continue();
    });
    await page.reload();
    await expect.poll(() => expired).toBe(true);

    await page.getByRole("button", { name: "Reproducir" }).first().click();
    finishRenewal();

    await expect.poll(async () => (await playerState(page)).time, { timeout: 20_000 }).toBeGreaterThan(1);
    await expect(page.getByRole("button", { name: "Pausar" })).toBeVisible();
    browserErrors.length = 0; // The 403 above is logged by the browser on purpose.
  });
});
