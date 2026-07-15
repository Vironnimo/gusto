// Kleine Progressive-Enhancement-Helfer (die App funktioniert auch ohne JS).

// Auf grossen Bildschirmen und ohne JavaScript bleiben die Filter sichtbar.
// Nur auf dem Handy starten sie kompakt; eine aktive Auswahl bleibt offen,
// damit der aktuelle Zustand nicht versteckt wird.
(function () {
  const panel = document.querySelector(".filter-panel");
  if (!panel || !window.matchMedia("(max-width: 720px)").matches) return;
  if (panel.dataset.selected !== "true") panel.removeAttribute("open");
})();

// "/" fokussiert die Suche.
document.addEventListener("keydown", (e) => {
  const tag = (document.activeElement.tagName || "").toLowerCase();
  if (e.key === "/" && tag !== "input" && tag !== "textarea") {
    const s = document.querySelector('input[type="search"]');
    if (s) { e.preventDefault(); s.focus(); }
  }
});

// Live-Suche: waehrend man tippt, werden Ergebnisse UND Tag-Leiste ohne Submit
// aktualisiert. Wir holen entprellt denselben Endpunkt (/?q=...&tag=...) und
// tauschen nur die betroffenen Bereiche – so bleibt die volle Suchlogik des
// Servers erhalten (auch Zutaten im Markdown). Ohne JS bleibt das GET-Formular.
(function () {
  const form = document.querySelector("form.filters");
  const input = form && form.querySelector('input[name="q"]');
  const results = document.getElementById("results");
  const tagfilter = document.getElementById("tagfilter");
  if (!form || !input || !results) return;

  let seq = 0;
  let timer = null;

  async function update() {
    const params = new URLSearchParams(window.location.search); // aktive Tags behalten
    params.set("q", input.value);
    const url = "/?" + params.toString();
    const mine = ++seq;
    try {
      const resp = await fetch(url);
      const html = await resp.text();
      if (mine !== seq) return; // eine neuere Eingabe hat gewonnen
      const doc = new DOMParser().parseFromString(html, "text/html");
      const neu = doc.getElementById("results");
      const neueTags = doc.getElementById("tagfilter");
      if (neu) {
        results.classList.add("is-live"); // Karten-Einblendung beim Tippen aus
        results.innerHTML = neu.innerHTML;
      }
      if (neueTags && tagfilter) tagfilter.innerHTML = neueTags.innerHTML;
      history.replaceState(null, "", url); // URL/Lesezeichen aktuell halten
    } catch (e) {
      /* offline o.Ae.: das normale Submit bleibt als Fallback nutzbar */
    }
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(update, 150);
  }

  input.addEventListener("input", schedule);  // tippen + natives "x"-Loeschen
  input.addEventListener("search", schedule); // Clear-Button mancher Browser
  form.addEventListener("submit", (e) => { e.preventDefault(); clearTimeout(timer); update(); });
})();
