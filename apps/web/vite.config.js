import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [tailwindcss()],
  server: { proxy: { '/api': 'http://127.0.0.1:8766' } },
  build: { outDir: '../device-mac/dongdong/static', emptyOutDir: true },
});
