/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_TICK_URL?: string;
  readonly VITE_INSIGHT_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
