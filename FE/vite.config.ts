import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig, type Plugin } from 'vite'

/**
 * Figma html-to-design 캡처 스크립트는 개발 서버(npm run dev)의 index.html 에만 넣는다 (body 끝, 예전과 같은 태그).
 * 운영 빌드(dist/index.html)에 들어가면 외부 코드가 같은 출처 권한으로 실행되어
 * localStorage 의 로그인 토큰·비밀번호 입력·지갑 데이터를 읽을 수 있다. (vite build·preview 에는 적용되지 않음)
 * 개발 서버에서도 같은 권한으로 실행되므로 화면을 Figma 로 옮길 때만 켠다: FIGMA_CAPTURE=1 npm run dev
 */
const figmaCaptureDevOnly: Plugin = {
  name: 'figma-capture-dev-only',
  apply: 'serve',
  transformIndexHtml: () => [
    {
      tag: 'script',
      attrs: { src: 'https://mcp.figma.com/mcp/html-to-design/capture.js', async: true },
      injectTo: 'body',
    },
  ],
}

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss(), ...(process.env.FIGMA_CAPTURE === '1' ? [figmaCaptureDevOnly] : [])],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:3000',
        changeOrigin: true,
      },
    },
  },
})
