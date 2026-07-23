import { resolve } from "node:path";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, externalizeDepsPlugin } from "electron-vite";

export default defineConfig({
  main: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: resolve("src/main/index.ts")
      }
    }
  },
  preload: {
    plugins: [externalizeDepsPlugin()],
    build: {
      rollupOptions: {
        input: {
          control: resolve("src/preload/control.ts"),
          overlay: resolve("src/preload/overlay.ts"),
          capture: resolve("src/preload/capture.ts")
        }
      }
    }
  },
  renderer: {
    root: resolve("src/renderers"),
    plugins: [tailwindcss(), react()],
    build: {
      rollupOptions: {
        input: {
          control: resolve("src/renderers/control/index.html"),
          overlay: resolve("src/renderers/overlay/index.html"),
          capture: resolve("src/renderers/capture/index.html")
        }
      }
    }
  }
});
