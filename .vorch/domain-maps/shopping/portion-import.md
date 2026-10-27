# Portion preview and ingredient import

`shopping_preview_recipe` reads the recipe and its content under the catalog lock. It returns source/target servings, a factor, the recipe edit revision and an ordered list of original/scaled ingredient text with warnings. An explicit target is a positive integer and needs a stored source serving count. `shopping_add_recipe` requires the preview revision when a target is provided; it checks the revision under catalog and shopping locks and uses the same calculation. The existing visible-source duplicate guard still applies. Normal unscaled imports remain available without a preview.

The private `_ingredient_scaling.py` uses exact fractions for leading integers, decimal numbers, simple/mixed or Unicode fractions and clear ranges. It never rounds quantities. Ambiguous numeric expressions, multiple amounts and package sizes stay unchanged with a warning. Unquantified lines also remain unchanged and are identified when the factor differs from one. Recipe Markdown itself is unchanged.

`tests/test_shopping.py` owns scaling, stale-preview and import-guard contracts. API, CLI and browser tests cover transport and the review/confirm flow.
