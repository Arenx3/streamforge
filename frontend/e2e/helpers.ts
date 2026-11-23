import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { expect, test as base, type APIRequestContext, type Page } from "@playwright/test";

const MAILPIT = process.env.E2E_MAILPIT_URL ?? "http://localhost:8025";
const HERE = dirname(fileURLToPath(import.meta.url));

export const PASSWORD = "marquesina-roja-42";
export const SAMPLE = "Muestra de StreamForge";
export const sampleVideo = () => readFileSync(join(HERE, "fixtures", "sample.mp4"));
export const unique = (prefix: string) =>
  `${prefix}-${Date.now().toString(36)}${Math.random().toString(36).slice(2, 6)}`;

/** Every test fails on a browser error unless it clears the ones it provokes on purpose. */
export const test = base.extend<{ browserErrors: string[] }>({
  browserErrors: [
    async ({ page }, use) => {
      const errors: string[] = [];
      page.on("console", (message) => {
        if (message.type() === "error") errors.push(message.text());
      });
      page.on("pageerror", (error) => errors.push(error.message));
      await use(errors);
      expect(errors).toEqual([]);
    },
    { auto: true },
  ],
});

export { expect };

export async function enterDemo(page: Page) {
  await page.goto("/login");
  await page.getByRole("button", { name: "Entrar a la demo" }).click();
  await expect(page.getByRole("heading", { name: "Biblioteca" })).toBeVisible();
}

export const videoRow = (page: Page, title: string) =>
  page.locator("tbody tr").filter({ hasText: title });

export const stateIn = (page: Page, title: string, state: string) =>
  videoRow(page, title).locator("span:visible", { hasText: state }).first();

export async function waitForReady(page: Page, title: string) {
  await expect(stateIn(page, title, "Listo")).toBeVisible({ timeout: 150_000 });
}

export async function chooseFile(page: Page, name: string, buffer: Buffer, mimeType = "video/mp4") {
  await page.locator('input[type="file"]').setInputFiles({ name, mimeType, buffer });
}

export async function uploadSample(page: Page, title: string) {
  await page.getByRole("link", { name: "Subir video" }).first().click();
  await chooseFile(page, `${title}.mp4`, sampleVideo());
  await expect(page.getByRole("link", { name: "Ver video" }).first()).toBeVisible({
    timeout: 60_000,
  });
}

export async function emailLink(request: APIRequestContext, to: string, subject: string) {
  let link: string | undefined;
  await expect
    .poll(
      async () => {
        const query = encodeURIComponent(`to:${to}`);
        const search = await (await request.get(`${MAILPIT}/api/v1/search?query=${query}`)).json();
        const found = (search.messages ?? []).find((message: { Subject: string }) =>
          message.Subject.startsWith(subject),
        );
        if (!found) return false;
        const detail = await (await request.get(`${MAILPIT}/api/v1/message/${found.ID}`)).json();
        link = /https?:\S+#token=\S+/.exec(detail.Text)?.[0];
        return Boolean(link);
      },
      { timeout: 30_000 },
    )
    .toBe(true);
  // The stack signs links for its public URL; the test may run on another port.
  const url = new URL(link!);
  return `${url.pathname}${url.hash}`;
}

export const playerState = (page: Page) =>
  page.evaluate(() => {
    const video = document.querySelector("video")!;
    return {
      time: video.currentTime,
      paused: video.paused,
      muted: video.muted,
      width: video.videoWidth,
    };
  });
