import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
// https://vitejs.dev/config/
export default defineConfig({
    plugins: [react()],
    resolve: {
        alias: {
            "@": path.resolve(__dirname, "./src"),
        },
    },
    server: {
        port: 5173,
        host: true,
        proxy: {
            // Forwards frontend calls to /api/* straight to the FastAPI
            // container during local development, avoiding CORS entirely.
            "/api": {
                target: "http://api:8000",
                changeOrigin: true,
            },
        },
    },
});
