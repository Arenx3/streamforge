import { describe, expect, it } from "vitest";

import type { VideoStatus } from "./api";
import { serialOf, STAGE_SHARE, videoJourney } from "./VideoTicket";

const details = (status: VideoStatus, progress = 0, error_message = "") =>
  videoJourney({ status, progress, error_message }).stages.map(({ detail }) => detail);

describe("videoJourney", () => {
  it("names the real state of each stage", () => {
    expect(details("PENDING_UPLOAD")).toEqual(["Sin terminar", "Pendiente", "Pendiente"]);
    expect(details("UPLOADED")).toEqual(["Completa", "En cola", "Pendiente"]);
    expect(details("PROCESSING", 47)).toEqual(["Completa", "47 %", "Pendiente"]);
    expect(details("FAILED", 31)).toEqual(["Completa", "Falló", "Cancelada"]);
    expect(details("READY", 100)).toEqual(["Completa", "Completo", "Lista"]);
  });

  it("marks an unfinished upload like the library does", () => {
    const [upload] = videoJourney({ status: "PENDING_UPLOAD", progress: 0 }).stages;

    expect(upload.state).toBe("unfinished");
  });

  it("fills the line under the stage in course", () => {
    expect(videoJourney({ status: "UPLOADED", progress: 0 }).percent).toBeCloseTo(STAGE_SHARE);
    expect(videoJourney({ status: "PROCESSING", progress: 50 }).percent).toBeCloseTo(50);
    expect(videoJourney({ status: "READY", progress: 100 }).percent).toBe(100);
  });

  it("only moves forward along the pipeline", () => {
    const trip = [
      videoJourney({ status: "PENDING_UPLOAD", progress: 0 }),
      videoJourney({ status: "UPLOADED", progress: 0 }),
      videoJourney({ status: "PROCESSING", progress: 1 }),
      videoJourney({ status: "PROCESSING", progress: 99 }),
      videoJourney({ status: "READY", progress: 100 }),
    ].map(({ percent }) => percent);

    expect(trip).toEqual([...trip].sort((a, b) => a - b));
  });

  it("keeps brass for what is really advancing", () => {
    expect(videoJourney({ status: "PROCESSING", progress: 5 }).tone).toBe("live");
    for (const status of ["PENDING_UPLOAD", "UPLOADED", "READY", "FAILED"] as const) {
      expect(videoJourney({ status, progress: 5 }).tone).not.toBe("live");
    }
  });

  it("does not claim an abandoned upload was completed", () => {
    expect(details("FAILED", 0, "Upload abandoned before it was completed.")).toEqual([
      "Falló",
      "Cancelado",
      "Cancelada",
    ]);
  });
});

describe("serialOf", () => {
  it("prints the start of the real video id", () => {
    expect(serialOf("4f1c2a9e-1111-2222-3333-444455556666")).toBe("4F1C2A");
  });
});
