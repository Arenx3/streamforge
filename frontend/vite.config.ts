import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

export default defineConfig({
  plugins: [react(), tailwindcss()],
  // Fonts stay as files: the Content-Security-Policy does not allow data: fonts.
  build: { assetsInlineLimit: 0 },
  server: {
    port: 5173,
    // During `npm run dev`, the API comes from the Docker stack.
    proxy: { "/api": "http://localhost" },
  },
  test: {
    environment: "jsdom",
    setupFiles: "./src/test/setup.ts",
    css: false,
    restoreMocks: true,
  },
});
