import path from "node:path";

import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  output: "standalone",
  outputFileTracingRoot: path.join(__dirname, ".."),
  // Next 16 auto-writes web/AGENTS.md + web/CLAUDE.md on every `next dev`/`next build`; disabled
  // — the repo's canonical CLAUDE.md is at the root (CLAUDE.md), and these files are not part of
  // this task's Interfaces block (implementer judgment call, m3 task-03).
  agentRules: false,
};

export default nextConfig;
