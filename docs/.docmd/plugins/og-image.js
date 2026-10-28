// docmd plugin: emit the Open Graph / Twitter card meta tags on every docs
// page, wired to the site's shared social card.
//
// docmd's bundled `seo` plugin (not installed here) covers title, description
// and canonical; this plugin adds the pieces the docs pages were missing:
// og:image with its declared dimensions and alt text, and twitter:image.
// The absolute image URL matches the tags the landing page and the compat
// page carry (see apps/web/src/layouts/CompatLayout.astro), so all three
// surfaces of pyscriptc.pages.dev share one preview.
const SITE_URL = "https://pyscriptc.pages.dev";
const OG_IMAGE_PATH = "/og-image.png";
const OG_IMAGE_ALT =
  "pyscriptc: Compile annotated Python to a native binary. Terminal output of a pyscriptc compile run on an obsidian card.";

export const plugin = {
  name: "og-image",
  version: "0.1.0",
  capabilities: ["head"],
};

export function generateMetaTags(config, pageData) {
  const frontmatter = pageData?.frontmatter ?? {};
  if (frontmatter.noindex || frontmatter.seo?.noindex || frontmatter.components?.meta === false) {
    return "";
  }
  const image = frontmatter.image || frontmatter.seo?.image || `${SITE_URL}${OG_IMAGE_PATH}`;
  return [
    `<meta property="og:image" content="${image}">`,
    `<meta property="og:image:width" content="1200">`,
    `<meta property="og:image:height" content="630">`,
    `<meta property="og:image:alt" content="${OG_IMAGE_ALT}">`,
    `<meta name="twitter:image" content="${image}">`,
    "",
  ].join("\n");
}
