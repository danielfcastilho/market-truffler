import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  async redirects() {
    return [
      // Sniffer's former Truffles dashboard (/sniffer, briefly
      // /sniffer/truffles) is gone — rankings belong to OINK CORP — and
      // the Sniffs matrix moved from /sniffer/sniffs to /sniffer itself.
      {
        source: "/sniffer/truffles",
        destination: "/sniffer",
        permanent: false,
      },
      {
        source: "/sniffer/sniffs",
        destination: "/sniffer",
        permanent: false,
      },
    ];
  },
};

export default nextConfig;
