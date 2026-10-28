/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  // Several dev servers can run against this directory at once; they would share
  // one build output and overwrite each other's client chunks. Keeping a runtime's
  // output in its own directory stops that, and with it the silently stale API base.
  distDir: process.env.NEXT_DEV_DIST_DIR || '.next',
  env: {
    NEXT_PUBLIC_API_URL: process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000',
  },
}

module.exports = nextConfig
