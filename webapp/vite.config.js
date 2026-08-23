import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// The daemon listens on ws://127.0.0.1:8830. During `vite dev` the app connects
// to it directly over WebSocket (see src/useDaemon.js). A future read API / blob
// HTTP route can be proxied here.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5180,
    strictPort: false,
  },
})
