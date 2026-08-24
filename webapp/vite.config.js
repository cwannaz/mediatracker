import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The daemon listens on ws://127.0.0.1:55030. During `vite dev` the app connects
// to it directly over WebSocket (see src/useDaemon.js). A future read API / blob
// HTTP route can be proxied here. Dev server port is kept in the project's
// assigned 55000-55100 band.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 55080,
    strictPort: false,
    // Archived article images are served by the daemon's blob route (port+1).
    proxy: { '/blob': { target: 'http://127.0.0.1:55031', changeOrigin: true } },
  },
})
