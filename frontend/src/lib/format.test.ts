import { describe, expect, it } from "vitest";

import { formatBytes, formatDuration, formatQuality } from "./format";

describe("formatBytes", () => {
  it.each([
    [0, "0 B"],
    [512, "512 B"],
    [1536, "1.5 KB"],
    [39 * 1024 ** 2, "39.0 MB"],
    [412 * 1024 ** 2, "412 MB"],
    [1.8 * 1024 ** 3, "1.8 GB"],
  ])("formats %d", (bytes, expected) => {
    expect(formatBytes(bytes)).toBe(expected);
  });

  it("shows a dash when the size is unknown", () => {
    expect(formatBytes(null)).toBe("—");
    expect(formatBytes(undefined)).toBe("—");
  });
});

describe("formatDuration", () => {
  it.each([
    [0, "0:00"],
    [9.4, "0:09"],
    [65, "1:05"],
    [3725, "1:02:05"],
  ])("formats %d seconds", (seconds, expected) => {
    expect(formatDuration(seconds)).toBe(expected);
  });

  it("shows a dash when the duration is unknown", () => {
    expect(formatDuration(null)).toBe("—");
  });
});

describe("formatQuality", () => {
  it("uses the short side", () => {
    expect(formatQuality(1920, 1080)).toBe("1080p");
    expect(formatQuality(1080, 1920)).toBe("1080p");
  });

  it("shows a dash until the video is probed", () => {
    expect(formatQuality(null, null)).toBe("—");
  });
});
