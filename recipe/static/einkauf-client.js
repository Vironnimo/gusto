// einkauf-client.js — Offline-fähiger Client der Küchenbuch-Einkaufsliste.
//
// Rendert die Liste aus localStorage (Quelle der Wahrheit lokal: der Pi),
// mutiert optimistisch offline und synct im Hintergrund per Voll-State-POST
// gegen /api/einkauf/sync. Nutzt exakt dieselben CSS-Klassen wie die
// server-gerenderte Phase-1-Version in einkauf.html — kein neues CSS nötig.
//
// Siehe docs/sync-kontrakt.md, Abschnitt "Client-Store + Verhalten (2C)".

(function () {
  "use strict";

  var STORAGE_KEY = "kuechenbuch.einkauf";
  var SYNC_URL = "/api/einkauf/sync";

  // --- localStorage-Anbindung ------------------------------------------------

  // Liefert immer ein Array von Items (inkl. Tombstones). Robust gegen
  // fehlenden / kaputten Speicher.
  function loadItems() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return [];
      var data = JSON.parse(raw);
      if (data && Array.isArray(data.items)) return data.items;
    } catch (e) {
      // Kaputter Speicher -> als leer behandeln, nächste Mutation/Sync heilt.
    }
    return [];
  }

  function saveItems(items) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ items: items }));
    } catch (e) {
      // Speicher voll/blockiert -> UI bleibt trotzdem konsistent (In-Memory).
    }
  }

  // Serverkonformer Zeitstempel: UTC, sekundengenau, literal Z, ohne ms.
  function jetztIso() {
    return new Date().toISOString().replace(/\.\d{3}Z$/, "Z");
  }

  // --- Rendering -------------------------------------------------------------

  var clientEl = null;
  var serverEl = null;

  function el(tag, className) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    return node;
  }

  // Baut eine <li class="eink-item"> exakt wie die Server-Version.
  function renderItem(item) {
    var li = el("li", "eink-item" + (item.checked ? " is-done" : ""));

    // Checkbox-"Form" (im Client nur ein Button, gleiche Klassen).
    var check = el("div", "eink-check");
    var box = el("button", "eink-box" + (item.checked ? " is-checked" : ""));
    box.type = "button";
    if (item.checked) {
      box.textContent = "✓"; // ✓
      box.setAttribute("aria-label", "Häkchen bei „" + item.text + "“ entfernen");
    } else {
      box.setAttribute("aria-label", "„" + item.text + "“ abhaken");
    }
    box.addEventListener("click", function () {
      toggleItem(item.id);
    });
    check.appendChild(box);

    // Körper: Text + optional Menge + optional Quelle.
    var body = el("div", "eink-body");
    var text = el("span", "eink-text");
    text.textContent = item.text;
    body.appendChild(text);

    if (item.menge) {
      var menge = el("span", "eink-menge");
      menge.textContent = item.menge;
      body.appendChild(menge);
    }

    if (item.quelle) {
      var quelle = el("a", "eink-quelle");
      quelle.href = "/rezept/" + item.quelle;
      quelle.textContent = "aus „" + item.quelle + "“";
      body.appendChild(quelle);
    }

    // Entfernen-Button (gleiche Klassen wie Server-Form).
    var remove = el("div", "eink-remove");
    var x = el("button", "eink-x");
    x.type = "button";
    x.textContent = "✕"; // ✕
    x.setAttribute("aria-label", "„" + item.text + "“ entfernen");
    x.addEventListener("click", function () {
      removeItem(item.id);
    });
    remove.appendChild(x);

    li.appendChild(check);
    li.appendChild(body);
    li.appendChild(remove);
    return li;
  }

  // Render aus localStorage: geloescht rausfiltern, offen/erledigt gruppieren,
  // nach erstellt_am aufsteigend sortieren.
  function render() {
    if (!clientEl) return;

    var items = loadItems().filter(function (it) {
      return !it.geloescht;
    });

    items.sort(function (a, b) {
      var av = a.erstellt_am || "";
      var bv = b.erstellt_am || "";
      if (av < bv) return -1;
      if (av > bv) return 1;
      return 0;
    });

    var offen = items.filter(function (it) {
      return !it.checked;
    });
    var erledigt = items.filter(function (it) {
      return it.checked;
    });

    clientEl.textContent = "";

    // Add-Formular (eigenes, gleicher Look wie Server-Form).
    clientEl.appendChild(renderAddForm());

    if (offen.length === 0 && erledigt.length === 0) {
      clientEl.appendChild(renderEmpty());
      return;
    }

    var board = el("div", "eink-board");

    // Gruppe "Offen" (immer sichtbar, mit Zähler).
    var grpOffen = el("section", "eink-group");
    var titleOffen = el("h2", "eink-group-title");
    titleOffen.appendChild(document.createTextNode("Offen "));
    var countOffen = el("span", "eink-count");
    countOffen.textContent = String(offen.length);
    titleOffen.appendChild(countOffen);
    grpOffen.appendChild(titleOffen);

    if (offen.length > 0) {
      var listOffen = el("ul", "eink-list");
      offen.forEach(function (it) {
        listOffen.appendChild(renderItem(it));
      });
      grpOffen.appendChild(listOffen);
    } else {
      var leer = el("p", "eink-leer");
      leer.textContent = "Alles erledigt — nichts mehr offen.";
      grpOffen.appendChild(leer);
    }
    board.appendChild(grpOffen);

    // Gruppe "Erledigt" (nur wenn vorhanden).
    if (erledigt.length > 0) {
      var grpDone = el("section", "eink-group eink-group-done");
      var titleDone = el("h2", "eink-group-title");
      titleDone.appendChild(document.createTextNode("Erledigt "));
      var countDone = el("span", "eink-count");
      countDone.textContent = String(erledigt.length);
      titleDone.appendChild(countDone);
      grpDone.appendChild(titleDone);

      var listDone = el("ul", "eink-list");
      erledigt.forEach(function (it) {
        listDone.appendChild(renderItem(it));
      });
      grpDone.appendChild(listDone);

      // "Erledigte entfernen" — eigener Form-Look (.eink-clear + .btn-text).
      var clearForm = el("div", "eink-clear");
      var clearBtn = el("button", "btn-text");
      clearBtn.type = "button";
      clearBtn.textContent = "Erledigte entfernen";
      clearBtn.addEventListener("click", function () {
        clearDone();
      });
      clearForm.appendChild(clearBtn);
      grpDone.appendChild(clearForm);

      board.appendChild(grpDone);
    }

    clientEl.appendChild(board);
  }

  // Eigenes Add-Formular des Clients (gleiche Klassen wie Server-Form).
  function renderAddForm() {
    var form = el("form", "eink-add");
    form.setAttribute("novalidate", "novalidate");

    var inputText = el("input", "eink-add-text");
    inputText.type = "text";
    inputText.name = "text";
    inputText.placeholder = "Was brauchst du? z.B. 200 g Spaghetti";
    inputText.autocomplete = "off";

    var inputMenge = el("input", "eink-add-menge");
    inputMenge.type = "text";
    inputMenge.name = "menge";
    inputMenge.placeholder = "Menge";
    inputMenge.autocomplete = "off";

    var btn = el("button", "btn");
    btn.type = "submit";
    btn.textContent = "Hinzufügen";

    form.appendChild(inputText);
    form.appendChild(inputMenge);
    form.appendChild(btn);

    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var text = inputText.value.trim();
      if (!text) {
        inputText.focus();
        return;
      }
      var menge = inputMenge.value.trim();
      addItem(text, menge);
      inputText.value = "";
      inputMenge.value = "";
      inputText.focus();
    });

    return form;
  }

  // Leer-Zustand exakt wie Server (.empty + 🧺).
  function renderEmpty() {
    var wrap = el("div", "empty");
    var emoji = el("p", "empty-emoji");
    emoji.textContent = "🧺"; // 🧺
    wrap.appendChild(emoji);
    var p = el("p");
    p.textContent =
      "Die Einkaufsliste ist leer. Trag oben etwas ein oder schick die " +
      "Zutaten eines Rezepts hierher.";
    wrap.appendChild(p);
    return wrap;
  }

  // --- Mutationen ------------------------------------------------------------
  // Muster überall: localStorage schreiben -> neu rendern -> sync() anstoßen.

  function addItem(text, menge) {
    var items = loadItems();
    var now = jetztIso();
    items.push({
      id: crypto.randomUUID(),
      text: text,
      menge: menge || "",
      checked: false,
      quelle: null,
      erstellt_am: now,
      geaendert_am: now,
      geloescht: false,
    });
    saveItems(items);
    render();
    sync();
  }

  function toggleItem(id) {
    var items = loadItems();
    for (var i = 0; i < items.length; i++) {
      if (items[i].id === id) {
        items[i].checked = !items[i].checked;
        items[i].geaendert_am = jetztIso();
        break;
      }
    }
    saveItems(items);
    render();
    sync();
  }

  function removeItem(id) {
    var items = loadItems();
    for (var i = 0; i < items.length; i++) {
      if (items[i].id === id) {
        items[i].geloescht = true;
        items[i].geaendert_am = jetztIso();
        break;
      }
    }
    saveItems(items);
    render();
    sync();
  }

  function clearDone() {
    var items = loadItems();
    var now = jetztIso();
    items.forEach(function (it) {
      if (it.checked && !it.geloescht) {
        it.geloescht = true;
        it.geaendert_am = now;
      }
    });
    saveItems(items);
    render();
    sync();
  }

  // --- Sync ------------------------------------------------------------------
  // Voll-State: kompletten lokalen Stand (inkl. Tombstones) hochladen,
  // gemergte Antwort übernehmen. Fehlertolerant: offline / Netzfehler ->
  // lokalen Stand behalten.

  var syncing = false;
  var pending = false;

  // Server-Antwort in den AKTUELLEN lokalen Stand mergen — gleiche Regel wie
  // der Server (pro id gewinnt das groessere geaendert_am). So ueberschreibt
  // eine Sync-Antwort keine lokalen Aenderungen, die waehrend des laufenden
  // Requests passiert sind (sonst gingen schnelle Klicks verloren).
  function mergeInto(local, incoming) {
    var byId = {};
    local.forEach(function (it) { byId[it.id] = it; });
    incoming.forEach(function (rem) {
      var cur = byId[rem.id];
      if (!cur || (rem.geaendert_am || "") > (cur.geaendert_am || "")) {
        byId[rem.id] = rem;
      }
    });
    return Object.keys(byId).map(function (k) { return byId[k]; });
  }

  function sync() {
    if (!navigator.onLine) return;
    if (syncing) { pending = true; return; } // waehrend Sync geaendert -> nachziehen
    syncing = true;
    pending = false;

    var payload = JSON.stringify({ items: loadItems() });

    fetch(SYNC_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload,
    })
      .then(function (res) {
        if (!res.ok) throw new Error("sync failed: " + res.status);
        return res.json();
      })
      .then(function (data) {
        var incoming = data && Array.isArray(data.items) ? data.items : [];
        // gegen den AKTUELLEN Stand mergen, nicht blind ueberschreiben.
        saveItems(mergeInto(loadItems(), incoming));
        render();
      })
      .catch(function () {
        // Netzwerk-/Serverfehler -> lokalen Stand behalten, nicht rendern.
      })
      .then(function () {
        syncing = false;
        if (pending) sync(); // Aenderungen waehrend des Syncs nachziehen
      });
  }

  // --- Start -----------------------------------------------------------------

  function init() {
    serverEl = document.getElementById("eink-server");
    clientEl = document.getElementById("eink-client");
    if (!clientEl) return; // Ohne Container nichts tun (Server-Fallback bleibt).

    // JS aktiv: Server-Teil ausblenden, Client-Teil einblenden.
    if (serverEl) serverEl.hidden = true;
    clientEl.hidden = false;

    render();
    sync(); // Erststart (auch leer) füllt den lokalen Stand vom Server.
  }

  document.addEventListener("DOMContentLoaded", init);
  window.addEventListener("online", sync);
})();
