# Web: design

The design brief for Gusto's browser UI and where it is implemented. Entry point: `web.md` -> References. Implementation: `gusto/static/style.css`, one mobile-first stylesheet without a build step.

## Brief

"Gusto" is a warm cookbook editorial, deliberately not a dashboard: cream paper with fine grain, paprika red as accent, herb green as secondary color, Fraunces as display serif and Hanken Grotesk for text, numbered steps with large serif numerals, cards with a staggered fade-in. UI text and data fields are German.

## Tokens

Defined once on `:root` in `style.css`; reuse the variables instead of new literals.
- Paper `--paper #f6efe1` (page, manifest `background_color`), card paper `--paper-card #fcf7ec`, ink `--ink #2a201a`, soft ink `--ink-soft #7a6a59`, rules `--line #e6d9c2` and `--line-strong #d8c6a8`.
- Paprika accent `--accent #bf4528` (primary buttons, active states, eyebrows, step numerals, `theme-color`) and `--accent-deep #9c3318` (hover, error text).
- Herb green is `--olive #5f6b43` (tags, durations, quantities, checked boxes, archive badges); `--honey #cf9a3c` appears only in soft gradients and small accents.
- `--serif` Fraunces (optical size 9..144, weights 400 to 900 with italics) for brand, titles, recipe headings and numerals; `--sans` Hanken Grotesk (400 to 800, body weight 450). Fallbacks are Georgia and system-ui; both families load from Google Fonts (`web.md` -> External Dependencies).
- `--radius 14px`, warm brown shadows `--shadow` and `--shadow-lift`, content width `--maxw 1080px`.

## Signature elements

- Paper grain: a fixed SVG fractal-noise overlay on `body::before` (multiply, opacity .45); content sits above it.
- Page heads: uppercase letter-spaced accent `.eyebrow`, Fraunces 900 `.page-title`, muted `.lede`.
- Rendered recipe: first paragraph as italic serif lede, H2 with a trailing hairline, bullets as small paprika dots, ordered steps as large Fraunces numerals in paprika (`counter(step)`).
- Cards: paper cards with a paprika edge bar and a lift on hover; entry animation `rise` with `animation-delay: calc(var(--i)*45ms)`, where templates set `style="--i: {{ loop.index0 }}"`. Reduced motion and live-search swaps disable it.
- Buttons: `.btn` (paprika fill), `.btn-ghost` (outlined), `.btn-text` (quiet; destructive actions sit behind `details` or confirmations).
- Empty states: centered emoji, one sentence, one call to action. Errors: the `.error` box in accent tint.
- Brand mark: a paprika ceramic mortar and pestle with a basil leaf on cream, beside the wordmark (also on the standalone recovery page). The selected Image Generation master is `assets/branding/gusto-mortar.png`; `gusto/static/icons/make_icons.py` exports PNGs with Pillow, without OS fonts. It supplies browser favicons, the Apple touch icon and PWA icons, including a separately padded maskable version. All runtime icons are precached by `sw.js`; the master stays outside the runtime package.

## Layout and responsiveness

- At <= 720 px the navigation becomes a fixed bottom bar of six items with glyphs (`nav-symbol`) above the safe area, filters collapse into a summary, recipe actions become a two-column grid with a `Zum Rezept` jump, and touch targets are about 2.75rem (44 px). At <= 560 px spacing tightens and photo choices, image fields and the image grid stack. Facet groups use four columns on wide screens, two between 721 and 960 px, one on phones.
- Long tags, titles and Markdown media wrap or scroll inside their box; 320 px viewports must not scroll horizontally (`scripts/browser_check.py` asserts this for the favorites and shopping forms).
- Accessibility: skip link, `:focus-visible` paprika outline, `aria-current` on active navigation and chips, accessible chip labels, a live result count (`role="status"`).

## Copy

German, warm and informal: the user is addressed with `du`, household pages may speak as `wir`/`eure`. Destructive actions state their consequence and whether they can be undone (archive versus purge, clearing the shopping list).
