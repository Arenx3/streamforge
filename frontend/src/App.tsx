import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter, Outlet, Route, Routes } from "react-router";
import { Toaster } from "sonner";

import { AppShell } from "./components/layout/AppShell";
import { AccountMenu, UnverifiedBanner } from "./features/auth/AccountMenu";
import { AuthProvider } from "./features/auth/AuthProvider";
import { PublicOnly, RequireAuth } from "./features/auth/guards";
import {
  ForgotPasswordPage,
  LoginPage,
  RegisterPage,
  ResetPasswordPage,
  VerifyEmailPage,
} from "./features/auth/pages";
import { UploadDock, UploadPage, UploadWatcher } from "./features/upload/UploadPage";
import { useShowsPlayer } from "./features/videos/api";
import { LibraryPage } from "./features/videos/LibraryPage";
import { LiveUpdates } from "./features/videos/LiveUpdates";
import { SharedVideoPage } from "./features/videos/SharedVideoPage";
import { VideoPage } from "./features/videos/VideoPage";
import { NotFound } from "./pages/NotFound";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 10_000, refetchOnWindowFocus: false } },
});

function AppLayout() {
  // Watching a video: the rail steps aside so the video takes the room.
  const watching = useShowsPlayer();
  return (
    <AppShell account={<AccountMenu />} compact={watching}>
      <UnverifiedBanner />
      <Outlet />
      <LiveUpdates />
      <UploadWatcher />
      <UploadDock />
    </AppShell>
  );
}

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <BrowserRouter>
        <AuthProvider>
          <Routes>
            <Route element={<PublicOnly />}>
              <Route path="login" element={<LoginPage />} />
              <Route path="register" element={<RegisterPage />} />
              <Route path="forgot-password" element={<ForgotPasswordPage />} />
            </Route>
            <Route path="reset-password" element={<ResetPasswordPage />} />
            <Route path="verify-email" element={<VerifyEmailPage />} />
            <Route path="v/:token" element={<SharedVideoPage />} />
            <Route element={<RequireAuth />}>
              <Route element={<AppLayout />}>
                <Route index element={<LibraryPage />} />
                <Route path="upload" element={<UploadPage />} />
                <Route path="videos/:id" element={<VideoPage />} />
              </Route>
            </Route>
            <Route path="*" element={<NotFound />} />
          </Routes>
        </AuthProvider>
      </BrowserRouter>
      <Toaster
        theme="dark"
        position="bottom-right"
        toastOptions={{
          style: {
            background: "var(--color-overlay)",
            border: "1px solid var(--color-line-strong)",
            color: "var(--color-bone)",
            fontFamily: "var(--font-sans)",
          },
        }}
      />
    </QueryClientProvider>
  );
}
