/// <reference types="vite/client" />

interface ImportMetaEnv {
  /**
   * Operator API key sent as the X-Forge-Key header on every backend request.
   * Must match FORGE_API_KEY in the backend .env. Leave unset for local dev,
   * where the backend runs with FORGE_API_KEY empty and auth is disabled.
   */
  readonly VITE_FORGE_API_KEY?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
