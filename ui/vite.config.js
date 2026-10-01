import { defineConfig } from "vite";
export default defineConfig({
  base: "./",
  build: {
    outDir: "../plugin/workbench_static",
    emptyOutDir: true,
    sourcemap: false,
  },
});
