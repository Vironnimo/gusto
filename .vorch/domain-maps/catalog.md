# Catalog

The catalog domain owns active and archived recipes, their metadata and images, tag-based discovery, cooking history, suggestions, and consistency diagnostics.

## Overview

Catalog behavior lives in `gusto/core.py`. `gusto/api.py` exposes it to both
normal CLI and browser clients; surfaces only translate input and present
results. Shopping consumes recipe content only to extract ingredients.
Persisted content is split deliberately between portable Markdown and JSON
metadata.

## Terms

GLOSSARY → Rezeptarchiv defines the reversible default for recipe deletion.

### Facet

**Definition:** A tag category used as one filter dimension. Selected tags are ORed within one facet and ANDed across facets.

### Cover image

**Definition:** The recipe image selected for cards and the top of the detail page. If the stored selection is absent, the first image is the runtime fallback.

## Data Model

- `recipes/<slug>.md` is pure Markdown and begins with the recipe title as an H1; it contains no metadata or frontmatter.
- `data/recipes.json` is the catalog index. Each entry carries the stable slug, title, flat tag list, optional duration and servings, latest cooked date, image metadata, and optional cover-image id.
- `data/categories.json` owns tag-to-facet assignment and display order. A tag remains valid when unassigned, but `check` reports it as uncategorized.
- `images/<slug>/` contains Gusto-owned copies named from their image ids. Image metadata remains in the catalog index.
- `archive/<slug>/` is one self-contained reversible snapshot: `recipe.md`,
  `metadata.json` with archived timestamp and full `Recipe` data, and an
  optional owned `images/` folder.
- `data/log.json` is append-only cooking history in `{date, slug}` records.
  Archiving and purging do not rewrite historical log entries.

The slug joins Markdown, metadata, image storage, log entries, shopping sources, and URLs. Editing a title does not rename the slug.

## Interfaces

`gusto/core.py` exposes catalog reads and mutations, archive list/show/restore/
purge, active-or-archived reference lookup, search and facet grouping,
cooking-log and suggestion operations, image management, and the consistency
check. Callers receive `Recipe`, `ArchivedRecipe`, and `RecipeImage` objects or
JSON-ready dictionaries produced from them.

The API command registry exposes these capabilities to the CLI through `gusto
list|search|tags|show|new|edit|content set|cooked|log|suggest|check|set|delete`,
`gusto archive
list|show|restore|purge`, and `gusto image ...`. `delete` is the compatibility
verb for reversible archiving; only `archive purge --yes` means destruction.
`edit` and `content set` write bodies through `recipe.content.set`; normal CLI
commands never modify catalog files directly. Web catalog/archive pages and
form actions in `gusto/web.py` use the same Core.

`new` and a tag-changing `set` preserve tags that have no named facet but warn
immediately in both CLI presentation modes. JSON adds a `warnings` array with
the stable `uncategorized_tags` code and affected tags; those tags continue to
share the runtime `Sonstige` facet until `categories.json` assigns them.

Recipe titles must be non-empty. Core always makes the first Markdown H1 match
the metadata title when creating or setting a title, including when the same
title is re-applied to repair drift. Duration/servings, time caps, and log/
suggestion day ranges, when present, must be positive integers.
`update_recipe` has explicit clear flags for the optional
numeric values; the CLI exposes them through `gusto set --clear-duration` and
`--clear-servings`, and blank web edit fields use the same core path.

The recipe detail page links to web image management. It can add a photo from
the outward-facing camera or image library, edit role/caption, select the cover,
and remove an image. Both browser choices use the same core image operations as
the CLI.

Cooking-log writes accept only exact `YYYY-MM-DD` calendar dates no later than
today, so the log's lexical ordering remains valid. Suggestion limits must be
nonnegative; zero deliberately returns no candidates.

The shopping domain calls the catalog lookup and recipe-content reader when adding every bullet under `## Zutaten` to the shopping list; it does not own recipe parsing beyond that section rule.

## Conventions

- JSON writes use the shared atomic writer. New catalog behavior belongs in
  Core, then API/CLI, before web presentation.
- Every catalog mutation holds the cross-process catalog lock across its full
  read-modify-write transaction. This includes the cooking log and owned recipe
  images because both also update catalog state.
- Core create/update owns the metadata-title ↔ first-H1 invariant; web forms
  still reconstruct the line, but are not the source of truth for the rule.
- Images are copied into Gusto storage after extension and header/dimension validation. The first image becomes the cover unless another is explicitly selected.
- Before core receives a browser photo, the web shell applies EXIF orientation,
  limits the longest edge to 1920 px, converts it to WebP, and omits metadata.
  CLI imports travel as bounded API attachments and are copied in their
  supported original format, so the CLI client remains dependency-free.
- `images/_favorites/` is reserved for the shopping domain and must be ignored
  when diagnosing recipe image folders.
- `check` diagnoses active/archive H1, index, Markdown, image-file, image-folder,
  cover, interrupted archive transaction, active/archive collision, visible
  shopping source, favorite, and historical log-reference issues. It does not
  repair them. Uncategorized tags and unresolved append-only log history are
  warnings; other non-empty diagnostics are hard errors.

## Constraints & Gotchas

- Search reads both indexed metadata and the Markdown body, so ingredient text participates in full-text results.
- Separate clients may safely create recipes, record cooking, replace content,
  or import images concurrently without losing index/log/image metadata. Their
  final order can follow server lock acquisition order. Lock files must be
  created with their lockable byte atomically; pre-lock concurrent writes race
  on Windows. Direct live-store edits are outside the supported client
  contract.
- A duration limit excludes recipes whose duration is unknown, not only recipes over the limit.
- Suggestions intentionally only exclude recently cooked recipes and order the rest by oldest `last_cooked`; meal intelligence belongs to the calling agent.
- Removing the selected cover promotes the first remaining image.
- Archiving moves recipe-owned Markdown, metadata, and the whole image folder
  out of the active catalog. Restore rejects active file/slug collisions. Purge
  is irreversible and holds catalog plus shopping locks; it rejects the
  operation while visible shopping items reference the slug.
- Log and shopping records are not owned by an archive snapshot. Surfaces
  resolve their source slug to active or archived metadata; a purge may leave
  append-only log history showing its raw slug.
- Native camera capture is a browser hint (`environment`), not a custom live
  camera. A separate library action remains available when the hint is ignored;
  both uploads require a reachable server and have a 25 MB input limit.
- Verify catalog changes with `tests/test_recipes.py`, `tests/test_api.py`,
  `tests/test_cli.py`, and, for web-visible behavior, `scripts/browser_check.py`
  against throwaway data.
