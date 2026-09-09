import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true, // fail loudly rather than drifting to 5174, which CORS does not allow
  },
  resolve: {
    alias: {
      // Ketcher depends on Node's `events`; Vite otherwise stubs node builtins
      // with a browser-external shim whose EventEmitter is not constructable.
      events: 'events',
    },
  },
  optimizeDeps: {
    include: ['events'],
  },
  define: {
    // Ketcher's bundled code reads process.env at runtime; Vite does not shim it.
    'process.env': {},
    // Ketcher also references Node's `global`, which does not exist in a browser.
    global: 'globalThis',
  },
})
