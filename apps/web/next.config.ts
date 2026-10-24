import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  async redirects() {
    return [
      // /sniffer is now the experimental Rankings tab; the Sniffs matrix
      // (formerly /sniffer/sniffs, then /sniffer) lives at
      // /sniffer/market-data. The old /sniffer/truffles placeholder goes to
      // the rankings — no Truffle is qualified anywhere yet.
      {
        source: "/sniffer/truffles",
        destination: "/sniffer",
        permanent: false,
      },
      {
        source: "/sniffer/sniffs",
        destination: "/sniffer/market-data",
        permanent: false,
      },
    ];
  },
};

export default nextConfig;
