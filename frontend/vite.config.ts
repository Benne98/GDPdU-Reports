import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

// Default: 5174 + API :8008. Mode "test" → 5175 + API :8009.
// Mode "fdd-merge" → 5176 + API :8010 (GDPdU + Mathis FDD stack, parallel).
// Mode "reporting-v2" → 5177 + API :8011 (robust reporting pipeline on cloned DB, parallel to 5176).
// Mode "reporting-v2-sandbox" → 5179 + API :8013 (independent copy of 5177 for parallel edits).
// Mode "v4" → 5178 + API :8012 (finssentials_v4 blank DB for GL-entity refactor testing).
// Mode "merged" → 5180 + API :8014 (unified stack: reporting-v2 + v4 setup + sandbox budget on cloned finssentials_merged).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const isTest = mode === "test";
  const isFddMerge = mode === "fdd-merge";
  const isReportingV2 = mode === "reporting-v2";
  const isReportingV2Sandbox = mode === "reporting-v2-sandbox";
  const isV4 = mode === "v4";
  const isMerged = mode === "merged";
  const devPort = Number(
    env.VITE_DEV_PORT ||
      (isMerged ? 5180 : isV4 ? 5178 : isReportingV2Sandbox ? 5179 : isReportingV2 ? 5177 : isFddMerge ? 5176 : isTest ? 5175 : 5174),
  );
  const apiTarget = (
    env.VITE_DEV_API_PROXY ||
    (isMerged
      ? "http://127.0.0.1:8014"
      : isV4
      ? "http://127.0.0.1:8012"
      : isReportingV2Sandbox
      ? "http://127.0.0.1:8013"
      : isReportingV2
      ? "http://127.0.0.1:8011"
      : isFddMerge ? "http://127.0.0.1:8010" : isTest ? "http://127.0.0.1:8009" : "http://127.0.0.1:8008")
  ).replace(/\/$/, "");

  const rasaTarget = (
    env.VITE_RASA_PROXY ||
    (isFddMerge ? "http://127.0.0.1:5015" : "http://127.0.0.1:5005")
  ).replace(/\/$/, "");

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
