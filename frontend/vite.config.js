import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": process.env.SIGNAGE_API || "http://localhost:8000" } },
});
