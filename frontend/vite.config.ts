import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// Default: 5174 + API :8008. Mode "test" → 5175 + API :8009.
// Mode "fdd-merge" → 5176 + API :8010 (GDPdU + Mathis FDD stack, parallel).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const isTest = mode === "test";
  const isFddMerge = mode === "fdd-merge";
  const devPort = Number(
    env.VITE_DEV_PORT || (isFddMerge ? 5176 : isTest ? 5175 : 5174),
  );
  const apiTarget = (
    env.VITE_DEV_API_PROXY ||
    (isFddMerge ? "http://127.0.0.1:8010" : isTest ? "http://127.0.0.1:8009" : "http://127.0.0.1:8008")
  ).replace(/\/$/, "");

  const rasaTarget = (env.VITE_RASA_PROXY || "http://127.0.0.1:5005").replace(/\/$/, "");

  return {
    plugins: [react()],
    server: {
      host: true,
      port: devPort,
      strictPort: true,
      proxy: {
        "/api": {
          target: apiTarget,
          changeOrigin: true,
          timeout: 900_000,
          proxyTimeout: 900_000,
        },
        // Mathis FDD stack: rasa/ on :5005, actions on :5055 (NOT rasa-expert :5006).
        "/rasa": {
          target: rasaTarget,
          changeOrigin: true,
          rewrite: (path: string) => path.replace(/^\/rasa/, "") || "/",
        },
      },
    },
  };
});
