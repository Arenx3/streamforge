import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { fitPitch, Perforation } from "./Perforation";

describe("Perforation", () => {
  it("exposes real progress to assistive technology", () => {
    render(<Perforation value={47} label="Procesando video" />);

    const bar = screen.getByRole("progressbar", { name: "Procesando video" });
    expect(bar).toHaveAttribute("aria-valuenow", "47");
    expect(bar.style.getPropertyValue("--progress")).toBe("47%");
  });

  it.each([
    [-5, "0"],
    [140, "100"],
    [33.6, "34"],
  ])("keeps %d inside the range", (value, expected) => {
    render(<Perforation value={value} label="Avance" />);

    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", expected);
  });

  it("marks its tone", () => {
    render(<Perforation value={31} label="Falló" tone="failed" />);

    expect(screen.getByRole("progressbar")).toHaveAttribute("data-tone", "failed");
  });
});

describe("fitPitch", () => {
  it.each([64, 120, 333, 350, 700.5, 1078])("ends flush on a %dpx line", (width) => {
    const gaps = (width - 6) / fitPitch(width);

    expect(gaps).toBeCloseTo(Math.round(gaps));
  });

  it("never packs the dots tighter than the design", () => {
    for (let width = 15; width < 1400; width += 7) {
      expect(fitPitch(width)).toBeGreaterThanOrEqual(9);
    }
  });

  it("falls back to the design spacing on a line too short to fit", () => {
    expect(fitPitch(0)).toBe(9);
    expect(fitPitch(10)).toBe(9);
  });
});
