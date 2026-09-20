import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  async redirects() {
    return [
      // /sniffer/truffles was a short-lived standalone route before
      // Dashboard and Truffles were merged into one page at /sniffer.
      {
        source: "/sniffer/truffles",
        destination: "/sniffer",
        permanent: false,
      },
    ];
  },
};

export default nextConfig;
