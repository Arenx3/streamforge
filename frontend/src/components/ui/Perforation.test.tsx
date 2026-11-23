import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { Perforation } from "./Perforation";

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
