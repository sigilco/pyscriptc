# The pyscriptc Open Graph card

`og-image.html` is the template; `render.mjs` renders it to
`../public/og-image.png`. Both are committed, so the card is reproducible:
same template, same bytes.

## Regenerate

    node og/render.mjs

Run it from `apps/web/` (`pnpm --dir apps/web exec node og/render.mjs` also
works). It needs `playwright-cli` on PATH and Node 22+; no dependency is added
to the web app.

The script:

1. serves the web app root over a local HTTP server,
2. opens the template in a playwright-cli browser session,
3. fails if the card is not exactly 1200x630 or any block overflows the card,
4. screenshots `#og-card` and writes it to `apps/web/public/og-image.png`.

Commit the PNG together with any template change. The PNG is the only copy the
deployed site reads.

## Editing the template

Every color is a token mirrored from `src/styles/theme.css` (the obsidian
canvas, the ink ladder, the logo blue). Keep it that way: the card must follow
the site palette. The compile snippet in the terminal is illustrative but its
shape is real: `pyscriptc compile sum_squares.py -o sum_squares` is the
documented quickstart command and prints the binary path on stdout.
