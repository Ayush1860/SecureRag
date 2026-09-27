import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// GitHub Pages can't send response headers, so the Pages build carries its CSP as a <meta> tag
// (set CSP_CONNECT_SRC to the API origin). A <meta> CSP ignores frame-ancestors; amplify.yml sends
// the full header set when the app is hosted on Amplify instead.
function metaCsp(connectSrc) {
  const csp = [
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com",
    "img-src 'self' data:",
    `connect-src 'self' ${connectSrc}`,
    "base-uri 'self'",
    "form-action 'self'",
    "object-src 'none'",
  ].join('; ');
  return {
    name: 'meta-csp',
    apply: 'build',
    transformIndexHtml(html) {
      return html.replace(
        '<meta charset="UTF-8">',
        `<meta charset="UTF-8">\n  <meta http-equiv="Content-Security-Policy" content="${csp}">` +
          '\n  <meta name="referrer" content="strict-origin-when-cross-origin">',
      );
    },
  };
}

const connectSrc = (process.env.CSP_CONNECT_SRC || '').replace(/\/$/, '');

export default defineConfig({
  // GitHub Pages serves a project site under /<repo>/.
  base: process.env.VITE_BASE_PATH || '/',
  plugins: [react(), ...(connectSrc ? [metaCsp(connectSrc)] : [])],
  server: {
    port: 5173,
    host: '127.0.0.1',
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
});
