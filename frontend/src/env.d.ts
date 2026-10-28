/// <reference types="astro/client" />

interface ImportMetaEnv {
  /** Optional API base override for development; the standalone build calls /api/planner/v1 on its own origin. */
  readonly PUBLIC_PLANNER_API?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}