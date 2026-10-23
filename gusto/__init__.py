"""Rezept-System – Markdown-Rezepte, steuerbar per CLI und Web."""

__version__ = "0.1.9"

# Configure before Core, uploads, editors, or dependencies allocate scratch files.
from ._temp import configure_checkout_temp as _configure_checkout_temp

_configure_checkout_temp()
del _configure_checkout_temp
