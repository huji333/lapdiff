import { defineConfig } from "vitest/config";

// base を相対にして、dist/ をどのパスに置いても data/ を読めるようにする
export default defineConfig({
  base: "./",
  test: { include: ["src/**/*.test.ts"] },
});
