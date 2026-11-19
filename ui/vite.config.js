import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// `npm run dev` serves the page on :5173 and passes /api to `optcg web` on :8000.
// `npm run build` writes ui/dist, which `optcg web` serves by itself.
export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    proxy: { "/api": "http://127.0.0.1:8000" },
  },
});
