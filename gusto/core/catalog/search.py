"""Recipe queries over facets, metadata and Markdown."""
from __future__ import annotations

import gusto.core.records as records
import gusto.core._validation as validation
import gusto.core.catalog.categories as facets
import gusto.core.catalog.recipes as recipe_store


def search(query: str = "", match: str = "any", tags: list[str] | None = None,
           max_time: int | None = None) -> list[records.Recipe]:
    """Filter over metadata (tags, max_time) and, for `query`, additionally
    search the title, tags AND the Markdown content (so the ingredients too).

    Tag filter (facets): multiple `tags` are grouped by their category. A recipe
    matches if it has AT LEAST ONE of the selected tags in EVERY selected
    category -- i.e. OR within a category and AND across categories. Tags without
    a category form one shared group (OR among themselves)."""
    if not isinstance(query, str):
        raise ValueError("Der Suchbegriff muss ein Text sein.")
    if match not in ("any", "all"):
        raise ValueError("Der Suchmodus muss 'any' oder 'all' sein.")
    validation._positive_recipe_number(max_time, "Die maximale Dauer")
    terms = [t.lower() for t in query.split()]
    groups = _group_tags(tags or [])
    hits = []
    for r in recipe_store.load_recipes():
        if groups and not _matches_tags(r, groups):
            continue
        if max_time is not None and (r.duration_min is None or r.duration_min > max_time):
            continue
        if terms:
            metadata = f"{r.title} {' '.join(r.tags)}".lower()
            missing = [term for term in terms if term not in metadata]
            # Titles/tags often settle a search without opening the Markdown
            # file. For ALL, only the terms still missing need body matches.
            if missing and (match == "all" or len(missing) == len(terms)):
                body = r.content().lower()
                ok = (all(term in body for term in missing) if match == "all"
                      else any(term in body for term in missing))
                if not ok:
                    continue
        hits.append(r)
    return hits


def _group_tags(tags: list[str]) -> dict[str, set[str]]:
    """Group selected tags by category: key -> {tag-lowercase, ...}.
    Tags without a category go together under "__other__"."""
    mapping = facets._tag_to_category()
    groups: dict[str, set[str]] = {}
    for t in tags:
        tl = t.lower()
        groups.setdefault(mapping.get(tl, "__other__"), set()).add(tl)
    return groups


def _matches_tags(r: records.Recipe, groups: dict[str, set[str]]) -> bool:
    """OR within a group, AND across groups."""
    recipe_tags = {t.lower() for t in r.tags}
    return all(recipe_tags & selected for selected in groups.values())
