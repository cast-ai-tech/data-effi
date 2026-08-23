import type { NextConfig } from "next";

// Docker copies a self-contained server bundle; Vercel builds its own output and
// warns about this one. web/Dockerfile sets BUILD_TARGET=docker, so the same
// repository deploys to both without a branch or a second config file.
const isDockerBuild = process.env.BUILD_TARGET === "docker";

const nextConfig: NextConfig = {
  ...(isDockerBuild ? { output: "standalone" as const } : {}),
  reactStrictMode: true,
  poweredByHeader: false,

  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "no-referrer" },
          {
            key: "Permissions-Policy",
            value: "geolocation=(), microphone=(), camera=()",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
