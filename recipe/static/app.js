// Kleine Progressive-Enhancement-Helfer (die App funktioniert auch ohne JS).

// "/" fokussiert die Suche.
document.addEventListener("keydown", (e) => {
  const tag = (document.activeElement.tagName || "").toLowerCase();
  if (e.key === "/" && tag !== "input" && tag !== "textarea") {
    const s = document.querySelector('input[type="search"]');
    if (s) { e.preventDefault(); s.focus(); }
  }
});
