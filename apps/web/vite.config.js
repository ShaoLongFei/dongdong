import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [tailwindcss()],
  // 保留浏览器访问的 Host，后端才能正确校验同源请求。
  server: { proxy: { '/api': { target: 'http://127.0.0.1:8766', changeOrigin: false } } },
  build: { outDir: '../device-mac/dongdong/static', emptyOutDir: true },
});
