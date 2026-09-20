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

// Bestätigungsdialoge für destruktive Formulare: Der Nutzertext steht im
// data-confirm-Attribut des Formulars, nicht in einem JS-String im
// onsubmit-Attribut. HTML-Entities werden im Attribut zurück dekodiert, ein
// Apostroph im Titel würde den eingebetteten JS-String vorzeitig beenden und
// die Bestätigung still ausfallen lassen. Ohne JavaScript funktionieren die
// Formulare weiterhin ohne Dialog.
document.addEventListener("submit", (e) => {
  const form = e.target;
  if (!(form instanceof HTMLFormElement) || !form.dataset.confirm) return;
  if (!window.confirm(form.dataset.confirm)) {
    e.preventDefault();
    e.stopPropagation();
  }
});

// Doppel-Submit-Schutz fuer POST-Formulare: Core-Mutationen wie "Heute
// gekocht" deduplizieren bewusst nicht (zweimal an einem Tag kochen ist
// legitim), deshalb darf ein Doppelklick keinen zweiten Eintrag erzeugen.
// Ein zweiter Submit im selben Moment wird abgewiesen. Die Buttons werden
// erst NACH dem Submit-Event deaktiviert: Deaktiviert man den Submit-Button
// innerhalb des Events, bricht Chromium die laufende Submission ab und es
// waere gar nichts passiert. Nur POST-Formulare: bei GET-Formularen traegt
// der geklickte Button seinen Wert mit (Zeitraum-Schalter) - ein dort
// deaktivierter Button wuerde ihn aus der Anfrage verschlucken.
document.addEventListener("submit", (e) => {
  const form = e.target;
  if (!(form instanceof HTMLFormElement) || form.method !== "post") return;
  if (e.defaultPrevented) return; // z. B. abgelehnter Bestätigungsdialog
  if (form.dataset.submitted) {
    e.preventDefault(); // zweiter Klick im selben Moment: nur ein Eintrag
    return;
  }
  form.dataset.submitted = "1";
  setTimeout(() => {
    for (const button of form.querySelectorAll("button, input[type='submit']")) {
      if (button.type === "submit") button.disabled = true;
    }
  }, 0);
});

// Zurueck-Navigation aus dem Back-Forward-Cache stellt das alte DOM wieder
// her: das Formular muss dort erneut benutzbar sein.
window.addEventListener("pageshow", (e) => {
  if (!e.persisted) return;
  for (const form of document.querySelectorAll('form[data-submitted]')) {
    delete form.dataset.submitted;
    for (const button of form.querySelectorAll("button[disabled], input[disabled]")) {
      button.disabled = false;
    }
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

  async function update(fallbackToSubmit = false) {
    const params = new URLSearchParams(window.location.search); // aktive Tags behalten
    params.set("q", input.value);
    const url = "/?" + params.toString();
    const mine = ++seq;
    try {
      const resp = await fetch(url);
      if (mine !== seq) return; // eine neuere Eingabe hat gewonnen
      if (!resp.ok) {
        // Server-/Netzfehler: das klassische Submit liefert die
        // server-gerenderte Liste, statt die Suche stumm zu lassen.
        if (fallbackToSubmit) form.submit();
        return;
      }
      const html = await resp.text();
      if (mine !== seq) return; // eine neuere Eingabe hat gewonnen
      const doc = new DOMParser().parseFromString(html, "text/html");
      const neu = doc.getElementById("results");
      const neueTags = doc.getElementById("tagfilter");
      if (neu) {
        results.classList.add("is-live"); // Karten-Einblendung beim Tippen aus
        // No innerHTML with server data (project security rule): the already
        // parsed nodes are cloned over instead of copying markup through
        // strings, so server escaping stays the only source of markup.
        results.replaceChildren(
          ...Array.from(neu.childNodes, (node) => node.cloneNode(true))
        );
      }
      if (neueTags && tagfilter) {
        tagfilter.replaceChildren(
          ...Array.from(neueTags.childNodes, (node) => node.cloneNode(true))
        );
      }
      history.replaceState(null, "", url); // URL/Lesezeichen aktuell halten
    } catch (e) {
      // Verbindungsfehler: das klassische Submit bleibt der Fallback, damit
      // Enter weiterhin zur server-gerenderten Liste führt.
      if (mine !== seq) return; // eine neuere Eingabe hat gewonnen
      if (fallbackToSubmit) form.submit();
    }
  }

  function schedule() {
    clearTimeout(timer);
    timer = setTimeout(update, 150);
  }

  input.addEventListener("input", schedule);  // tippen + natives "x"-Loeschen
  input.addEventListener("search", schedule); // Clear-Button mancher Browser
  form.addEventListener("submit", (e) => { e.preventDefault(); clearTimeout(timer); update(true); });
})();

// Online-Aktualisierung: Der laufende Gusto-Dienst meldet erfolgreiche
// Fachmutationen mit einer monotonen Revision. Die erste Meldung ist nur der
// Ausgangsstand; spätere relevante Änderungen aktualisieren die offene Seite.
// Die offline-fähige Einkaufsliste übernimmt ihren Merge selbst.
(function () {
  if (!document.body.hasAttribute("data-gusto-live") || !window.EventSource) return;

  const pageResources = (() => {
    const path = window.location.pathname;
    if (path === "/shopping") return new Set(["shopping", "favorites", "catalog"]);
    if (path.startsWith("/favorites")) return new Set(["favorites"]);
    if (path === "/log") return new Set(["log", "catalog"]);
    if (path === "/suggestions") return new Set(["catalog", "log"]);
    if (path === "/archive" || path.startsWith("/archive/")) return new Set(["catalog"]);
    // Form-bearing pages (/new, /recipe/*/edit, /recipe/*/images) must not
    // auto-reload: a reload triggered by another client's change would wipe
    // unsaved input and interrupt running photo uploads. Plain view pages
    // keep reloading so external changes stay visible.
    if (path === "/"
        || (path.startsWith("/recipe/") && !/\/(edit|images)$/.test(path))) {
      return new Set(["catalog", "log"]);
    }
    return new Set();
  })();

  let latestRevision = Number(document.body.dataset.gustoRevision);
  if (!Number.isFinite(latestRevision)) latestRevision = 0;

  function connectLiveEvents() {
    const events = new EventSource(
      "/api/v1/events?after=" + encodeURIComponent(String(latestRevision))
    );
    events.onmessage = (event) => {
      let change;
      try {
        change = JSON.parse(event.data);
      } catch (_error) {
        return;
      }

      const revision = Number(change && change.revision);
      // A strictly smaller revision signals a server-side journal reset and
      // must be followed; only the exact revision this page already rendered
      // is skipped. Replays after a reconnect are genuinely missed changes,
      // so they must pass the filter too.
      if (!Number.isFinite(revision) || revision === latestRevision) return;
      latestRevision = revision;

      const resources = Array.isArray(change.resources) ? change.resources : [];
      if (!resources.some((resource) => pageResources.has(resource))) return;

      if (window.location.pathname === "/shopping") {
        document.dispatchEvent(new CustomEvent("gusto:change", { detail: change }));
        return;
      }
      window.location.reload();
    };
  }

  // Let the initial page and its images settle first. This keeps navigation
  // completion meaningful while the subsequent EventSource remains open.
  window.setTimeout(connectLiveEvents, 1000);
})();
