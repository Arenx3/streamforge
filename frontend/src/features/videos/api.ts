import {
  keepPreviousData,
  queryOptions,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { useMatch } from "react-router";

import { api } from "../../lib/api";
import { useLiveConnection } from "./liveEvents";

export type VideoStatus = "PENDING_UPLOAD" | "UPLOADED" | "PROCESSING" | "READY" | "FAILED";

export type Video = {
  id: string;
  title: string;
  description: string;
  status: VideoStatus;
  progress: number;
  error_message: string;
  thumbnail_url: string | null;
  duration_seconds: number | null;
  width: number | null;
  height: number | null;
  size_bytes: number | null;
  created_at: string;
  updated_at: string;
};

export type VideoDetail = Video & {
  processed_at: string | null;
  playback_url: string | null;
  share_token: string | null;
};

export type SharedVideo = Pick<
  Video,
  "title" | "description" | "duration_seconds" | "width" | "height" | "thumbnail_url"
> & { playback_url: string | null };

export const shareUrl = (token: string) => `${window.location.origin}/v/${token}`;

export type Page<T> = { count: number; next: string | null; previous: string | null; results: T[] };
export type VideoSummary = Record<VideoStatus, number>;

export const FILTERS = [
  { id: "todos", label: "Todos", statuses: [] },
  { id: "listos", label: "Listos", statuses: ["READY"] },
  { id: "en-proceso", label: "En proceso", statuses: ["UPLOADED", "PROCESSING"] },
  { id: "sin-terminar", label: "Sin terminar", statuses: ["PENDING_UPLOAD"] },
  { id: "con-error", label: "Con error", statuses: ["FAILED"] },
] as const satisfies readonly { id: string; label: string; statuses: readonly VideoStatus[] }[];

export type VideoFilter = (typeof FILTERS)[number];

export const PAGE_SIZE = 20;
/** Only used while the live connection is down. */
const POLL_MS = 3000;
export const isProcessing = (video: Pick<Video, "status">) =>
  video.status === "UPLOADED" || video.status === "PROCESSING";

export const countFor = (filter: VideoFilter, summary: VideoSummary | undefined) => {
  if (!summary) return undefined;
  const statuses: readonly VideoStatus[] = filter.statuses.length
    ? filter.statuses
    : (Object.keys(summary) as VideoStatus[]);
  return statuses.reduce((total, status) => total + summary[status], 0);
};

export function useVideos(filter: VideoFilter, page: number) {
  const search = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
  if (filter.statuses.length) search.set("status", filter.statuses.join(","));
  const live = useLiveConnection();
  return useQuery({
    queryKey: ["videos", "list", filter.id, page],
    queryFn: ({ signal }) => api<Page<Video>>(`/videos/?${search}`, { signal }),
    placeholderData: keepPreviousData,
    refetchInterval: (query) =>
      !live && query.state.data?.results.some(isProcessing) ? POLL_MS : false,
  });
}

export function useVideoSummary() {
  const live = useLiveConnection();
  return useQuery({
    queryKey: ["videos", "summary"],
    queryFn: ({ signal }) => api<VideoSummary>("/videos/summary/", { signal }),
    refetchInterval: (query) => {
      const data = query.state.data;
      return !live && data && data.UPLOADED + data.PROCESSING > 0 ? POLL_MS : false;
    },
  });
}

const videoQuery = (id: string | undefined, live: boolean) =>
  queryOptions({
    queryKey: ["videos", "detail", id],
    queryFn: ({ signal }) => api<VideoDetail>(`/videos/${id}/`, { signal }),
    enabled: Boolean(id),
    retry: false,
    refetchInterval: (query) =>
      !live && query.state.data && isProcessing(query.state.data) ? POLL_MS : false,
  });

export function useVideo(id: string | undefined) {
  return useQuery(videoQuery(id, useLiveConnection()));
}

/** Whether the page on screen is a video's player. */
export function useShowsPlayer() {
  const id = useMatch("/videos/:id")?.params.id;
  const { data = false } = useQuery({
    ...videoQuery(id, useLiveConnection()),
    select: (video) => video.status === "READY" && Boolean(video.playback_url),
  });
  return data;
}

/** A public link needs no session, and a stale one must not get in its way. */
export function useSharedVideo(token: string) {
  return useQuery({
    queryKey: ["shared", token],
    queryFn: ({ signal }) =>
      api<SharedVideo>(`/public/videos/${encodeURIComponent(token)}/`, { signal, auth: false }),
    retry: false,
  });
}

export function useVideoSharing(id: string) {
  const client = useQueryClient();
  const key = ["videos", "detail", id];
  const create = useMutation({
    mutationFn: () => api<VideoDetail>(`/videos/${id}/share/`, { method: "POST" }),
    onSuccess: (video) => client.setQueryData(key, video),
  });
  const revoke = useMutation({
    mutationFn: () => api<void>(`/videos/${id}/share/`, { method: "DELETE" }),
    onSuccess: () =>
      client.setQueryData<VideoDetail>(key, (video) => video && { ...video, share_token: null }),
  });
  return { create, revoke };
}

export function useUpdateVideo() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: string; title: string; description: string }) =>
      api<VideoDetail>(`/videos/${id}/`, { method: "PATCH", body }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["videos"] }),
  });
}

export function useDeleteVideo() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/videos/${id}/`, { method: "DELETE" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["videos"] }),
  });
}
