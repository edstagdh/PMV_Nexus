import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// Built files go to ../frontend (index.html + assets/) and are served by the
// backend at /. The whole folder is replaced on every build.
// File names are fixed (no content hashes): users update by extracting a zip
// over the project, so hashed names would leave old files piling up. The
// backend serves them with Cache-Control: no-cache + ETag, so browsers still
// pick up every change.
// Video.js player elements are web components, not Vue components.
const customElementTags = new Set(["video-player", "pmv-video-skin"]);

export default defineConfig({
  base: "/",
  plugins: [vue({ template: { compilerOptions: { isCustomElement: (tag) => customElementTags.has(tag) } } })],
  build: {
    outDir: "../frontend",
    emptyOutDir: true,
    assetsInlineLimit: 0,
    chunkSizeWarningLimit: 1200, // the Video.js player is most of the bundle
    rollupOptions: {
      output: {
        entryFileNames: "assets/app.js",
        // The player's translations load only when the browser's language needs one.
        chunkFileNames: (chunk) => (/[\\/]i18n[\\/]|[\\/]locales?[\\/]/.test(chunk.facadeModuleId || "") ? "assets/player-lang/[name].js" : "assets/[name].js"),
        assetFileNames: "assets/[name][extname]",
      },
    },
  },
  server: {
    // `npm run dev` serves the UI with hot reload and forwards API calls to a
    // running app on the default port.
    proxy: { "/api": "http://localhost:8676" },
  },
});
