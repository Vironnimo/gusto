# Catalog

The catalog domain owns recipes, their metadata and images, tag-based discovery, cooking history, suggestions, and consistency diagnostics.

## Overview

Catalog behavior lives in `gusto/core.py`. The CLI and web layers only translate user input and present core results; shopping consumes recipe content only to extract ingredients. Persisted content is split deliberately between portable Markdown and JSON metadata.

## Terms

No cross-cutting terms for this domain are currently defined in `.vorch/GLOSSARY.md`.

### Facet

**Definition:** A tag category used as one filter dimension. Selected tags are ORed within one facet and ANDed across facets.

### Cover image

**Definition:** The recipe image selected for cards and the top of the detail page. If the stored selection is absent, the first image is the runtime fallback.

## Data Model

- `recipes/<slug>.md` is pure Markdown and begins with the recipe title as an H1; it contains no metadata or frontmatter.
- `data/recipes.json` is the catalog index. Each entry carries the stable slug, title, flat tag list, optional duration and servings, latest cooked date, image metadata, and optional cover-image id.
- `data/categories.json` owns tag-to-facet assignment and display order. A tag remains valid when unassigned, but `check` reports it as uncategorized.
- `images/<slug>/` contains Gusto-owned copies named from their image ids. Image metadata remains in the catalog index.
- `data/log.json` is append-only cooking history in `{date, slug}` records. Deleting a recipe does not delete its historical log entries.

The slug joins Markdown, metadata, image storage, log entries, shopping sources, and URLs. Editing a title does not rename the slug.

## Interfaces

`gusto/core.py` exposes catalog reads and mutations, search and facet grouping, cooking-log and suggestion operations, image management, and the consistency check. Callers receive `Recipe` and `RecipeImage` objects or JSON-ready dictionaries produced from them.

The CLI exposes these capabilities through `gusto list|search|tags|show|new|edit|cooked|log|suggest|check|set|delete` and `gusto image ...`. Web catalog pages and form actions in `gusto/web.py` call the same core operations.

Recipe titles must be non-empty, and duration/servings, when present, must be
positive integers. `update_recipe` has explicit clear flags for the optional
numeric values; the CLI exposes them through `gusto set --clear-duration` and
`--clear-servings`, and blank web edit fields use the same core path.

The recipe detail page links to web image management. It can add a photo from
the outward-facing camera or image library, edit role/caption, select the cover,
and remove an image. Both browser choices use the same core image operations as
the CLI.

The shopping domain calls the catalog lookup and recipe-content reader when adding every bullet under `## Zutaten` to the shopping list; it does not own recipe parsing beyond that section rule.

## Conventions

- JSON writes use the shared atomic writer. New catalog behavior belongs in core before either surface.
- Every catalog mutation holds the cross-process catalog lock across its full
  read-modify-write transaction. This includes the cooking log and owned recipe
  images because both also update catalog state.
- Web create and edit always reconstruct the first Markdown line from the separate title field.
- Images are copied into Gusto storage after extension and header/dimension validation. The first image becomes the cover unless another is explicitly selected.
- Before core receives a browser photo, the web shell applies EXIF orientation,
  limits the longest edge to 1920 px, converts it to WebP, and omits metadata.
  CLI imports are copied in their supported original format so core/CLI remain
  dependency-free.
- `images/_favorites/` is reserved for the shopping domain and must be ignored
  when diagnosing recipe image folders.
- `check` diagnoses index, Markdown, tag, image-file, image-folder, and cover-selection inconsistencies; it does not repair them.

## Constraints & Gotchas

- Search reads both indexed metadata and the Markdown body, so ingredient text participates in full-text results.
- Separate Gusto processes may safely create recipes, record cooking, or import
  images concurrently without losing index/log/image metadata. Their final
  order can follow lock acquisition order. Direct Markdown/category edits and
  the interactive editor bypass the Core lock and remain caller-coordinated.
- A duration limit excludes recipes whose duration is unknown, not only recipes over the limit.
- Suggestions intentionally only exclude recently cooked recipes and order the rest by oldest `last_cooked`; meal intelligence belongs to the calling agent.
- Removing the selected cover promotes the first remaining image. Deleting a recipe removes its Markdown and image folder but preserves cooking history.
- Native camera capture is a browser hint (`environment`), not a custom live
  camera. A separate library action remains available when the hint is ignored;
  both uploads require a reachable server and have a 25 MB input limit.
- Verify catalog changes with `tests/test_recipes.py`, `tests/test_cli.py`, and, for web-visible behavior, `scripts/browser_check.py` against throwaway data.
