// docmd plugin: substitute {{NAME}} placeholders in page source before parse.
//
// PLACEHOLDERS maps a placeholder name to its environment variable and its
// default. PYSC_COMPAT_URL and PYSC_DOCS_URL follow the same shape, so a
// deployment can point the docs at another host without touching the pages.
//
// The same substitution runs in scripts/docs_placeholders.py, which
// `mise run docs:validate` uses to check links with the placeholders already
// expanded. Keep the two in sync.
const PLACEHOLDERS = {
  PYSC_COMPAT_URL: { env: "PYSC_COMPAT_URL", default: "https://pyscriptc-compat.pages.dev" },
  PYSC_DOCS_URL: { env: "PYSC_DOCS_URL", default: "https://pyscriptc.pages.dev" },
};

export const plugin = {
  name: "placeholders",
  version: "0.1.0",
  capabilities: ["build"],
};

export function onBeforeParse(src) {
  let out = src;
  for (const [name, spec] of Object.entries(PLACEHOLDERS)) {
    const value = process.env[spec.env] || spec.default;
    out = out.split(`{{${name}}}`).join(value);
  }
  return out;
}
