import { useQueryClient, type QueryClient, type QueryKey } from "@tanstack/react-query";
import { useEffect } from "react";

import type { Page, Video, VideoDetail } from "./api";
import { connectVideoEvents, type VideoEvent } from "./liveEvents";

/**
 * Patches cached data without making it look freshly fetched. Otherwise a progress
 * event would hide that a list is stale and is missing a newly uploaded video.
 */
function patchCached<T>(
  client: QueryClient,
  queryKey: QueryKey,
  update: (data: T) => T | undefined,
) {
  for (const query of client.getQueryCache().findAll({ queryKey })) {
    const data = query.state.data as T | undefined;
    const next = data === undefined ? undefined : update(data);
    if (next === undefined) continue;
    const wasInvalidated = query.state.isInvalidated;
    client.setQueryData(query.queryKey, next, { updatedAt: query.state.dataUpdatedAt });
    if (wasInvalidated) query.invalidate();
  }
}

export function applyVideoEvent(client: QueryClient, event: VideoEvent) {
  if (event.kind === "deleted") {
    client.removeQueries({ queryKey: ["videos", "detail", event.id] });
    void client.invalidateQueries({ queryKey: ["videos"] });
    return;
  }
  const { status, progress, error_message } = event;
  patchCached<Page<Video>>(client, ["videos", "list"], (page) =>
    page.results.some((video) => video.id === event.id)
      ? {
          ...page,
          results: page.results.map((video) =>
            video.id === event.id ? { ...video, status, progress, error_message } : video,
          ),
        }
      : undefined,
  );
  patchCached<VideoDetail>(client, ["videos", "detail", event.id], (video) => ({
    ...video,
    status,
    progress,
    error_message,
  }));
  // A state change brings new data: thumbnail, duration, playback URL and counts.
  if (event.kind === "state") void client.invalidateQueries({ queryKey: ["videos"] });
}

export function LiveUpdates() {
  const client = useQueryClient();

  useEffect(
    () =>
      connectVideoEvents({
        onReady: () => void client.invalidateQueries({ queryKey: ["videos"] }),
        onVideo: (event) => applyVideoEvent(client, event),
      }),
    [client],
  );

  return null;
}
