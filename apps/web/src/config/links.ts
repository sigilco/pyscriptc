export const siteLinks = {
  playground: import.meta.env.PUBLIC_PLAYGROUND_URL || "#playground",
  docs: import.meta.env.PUBLIC_DOCS_URL || "/docs/",
  compat: import.meta.env.PUBLIC_COMPAT_URL || "/compat/",
  github: import.meta.env.PUBLIC_GITHUB_URL || "https://github.com/sigilco/pyscriptc",
  pypi: import.meta.env.PUBLIC_PYPI_URL || "https://pypi.org/project/pyscriptc/",
  x: import.meta.env.PUBLIC_X_URL || "https://x.com/sigilco",
  privacy:
    import.meta.env.PUBLIC_PRIVACY_URL ||
    "https://github.com/sigilco/pyscriptc/blob/master/PRIVACY.md",
  license: import.meta.env.PUBLIC_LICENSE_URL || "https://github.com/sigilco/pyscriptc/blob/master/LICENSE",
};

export type SiteLinks = typeof siteLinks;
