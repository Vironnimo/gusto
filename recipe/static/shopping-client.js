// shopping-client.js — offline-capable client for the Gusto shopping list.
//
// Renders the list from localStorage (local source of truth: the Pi), mutates
// optimistically offline and syncs in the background via a full-state POST to
// /api/shopping/sync. Uses exactly the same CSS classes as the server-rendered
// version in shopping.html — no extra CSS needed.
//
// See docs/sync-kontrakt.md, section "Client store + behavior (2C)".

(function () {
  "use strict";

  var STORAGE_KEY = "gusto.shopping";
  var SYNC_URL = "/api/shopping/sync";

  // --- localStorage binding --------------------------------------------------

  // Always returns an array of items (incl. tombstones). Robust against a
  // missing / broken store.
  function loadItems() {
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      if (!raw) return [];
      var data = JSON.parse(raw);
      if (data && Array.isArray(data.items)) return data.items;
    } catch (e) {
      // Broken store -> treat as empty, the next mutation/sync heals it.
    }
    return [];
  }

  function saveItems(items) {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ items: items }));
    } catch (e) {
      // Store full/blocked -> UI stays consistent anyway (in memory).
    }
  }

  // Server-compatible timestamp: UTC with milliseconds and a literal Z.
  // For repeated changes to one item, always advance by at least 1 ms so an
  // immediate add-then-toggle cannot produce an ambiguous sync version.
  function nowIso(after) {
    var now = Date.now();
    var previous = Date.parse(after || "");
    if (!isNaN(previous) && now <= previous) now = previous + 1;
    return new Date(now).toISOString();
  }

  function isNewer(candidate, current) {
    var candidateMs = Date.parse(candidate || "");
    var currentMs = Date.parse(current || "");
    if (!isNaN(candidateMs) && !isNaN(currentMs)) {
      return candidateMs > currentMs;
    }
    return (candidate || "") > (current || "");
  }

  // --- Rendering -------------------------------------------------------------

  var clientEl = null;
  var serverEl = null;

  function el(tag, className) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    return node;
  }

  // Builds a <li class="shop-item"> exactly like the server version.
  function renderItem(item) {
    var li = el("li", "shop-item" + (item.checked ? " is-done" : ""));

    // Checkbox "form" (just a button on the client, same classes).
    var check = el("div", "shop-check");
    var box = el("button", "shop-box" + (item.checked ? " is-checked" : ""));
    box.type = "button";
    if (item.checked) {
      box.textContent = "✓";
      box.setAttribute("aria-label", "Häkchen bei „" + item.text + "“ entfernen");
    } else {
      box.setAttribute("aria-label", "„" + item.text + "“ abhaken");
    }
    box.addEventListener("click", function () {
      toggleItem(item.id);
    });
    check.appendChild(box);

    // Body: text + optional quantity + optional source.
    var body = el("div", "shop-body");
    var text = el("span", "shop-text");
    text.textContent = item.text;
    body.appendChild(text);

    if (item.quantity) {
      var quantity = el("span", "shop-quantity");
      quantity.textContent = item.quantity;
      body.appendChild(quantity);
    }

    if (item.source) {
      var source = el("a", "shop-source");
      source.href = "/recipe/" + item.source;
      source.textContent = "aus „" + item.source + "“";
      body.appendChild(source);
    }

    // Remove button (same classes as the server form).
    var remove = el("div", "shop-remove");
    var x = el("button", "shop-x");
    x.type = "button";
    x.textContent = "✕";
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

  // Render from localStorage: drop deleted, group open/done, sort by created_at
  // ascending.
  function render() {
    if (!clientEl) return;

    var items = loadItems().filter(function (it) {
      return !it.deleted;
    });

    items.sort(function (a, b) {
      var av = a.created_at || "";
      var bv = b.created_at || "";
      if (av < bv) return -1;
      if (av > bv) return 1;
      return 0;
    });

    var openItems = items.filter(function (it) {
      return !it.checked;
    });
    var doneItems = items.filter(function (it) {
      return it.checked;
    });

    clientEl.textContent = "";

    // Add form (own, same look as the server form).
    clientEl.appendChild(renderAddForm());

    if (openItems.length === 0 && doneItems.length === 0) {
      clientEl.appendChild(renderEmpty());
      return;
    }

    var board = el("div", "shop-board");

    // "Open" group (always visible, with counter).
    var grpOpen = el("section", "shop-group");
    var titleOpen = el("h2", "shop-group-title");
    titleOpen.appendChild(document.createTextNode("Offen "));
    var countOpen = el("span", "shop-count");
    countOpen.textContent = String(openItems.length);
    titleOpen.appendChild(countOpen);
    grpOpen.appendChild(titleOpen);

    if (openItems.length > 0) {
      var listOpen = el("ul", "shop-list");
      openItems.forEach(function (it) {
        listOpen.appendChild(renderItem(it));
      });
      grpOpen.appendChild(listOpen);
    } else {
      var empty = el("p", "shop-empty");
      empty.textContent = "Alles erledigt — nichts mehr offen.";
      grpOpen.appendChild(empty);
    }
    board.appendChild(grpOpen);

    // "Done" group (only if present).
    if (doneItems.length > 0) {
      var grpDone = el("section", "shop-group shop-group-done");
      var titleDone = el("h2", "shop-group-title");
      titleDone.appendChild(document.createTextNode("Erledigt "));
      var countDone = el("span", "shop-count");
      countDone.textContent = String(doneItems.length);
      titleDone.appendChild(countDone);
      grpDone.appendChild(titleDone);

      var listDone = el("ul", "shop-list");
      doneItems.forEach(function (it) {
        listDone.appendChild(renderItem(it));
      });
      grpDone.appendChild(listDone);

      // "Remove done" — own form look (.shop-clear + .btn-text).
      var clearForm = el("div", "shop-clear");
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

  // The client's own add form (same classes as the server form).
  function renderAddForm() {
    var form = el("form", "shop-add");
    form.setAttribute("novalidate", "novalidate");

    var inputText = el("input", "shop-add-text");
    inputText.type = "text";
    inputText.name = "text";
    inputText.placeholder = "Was brauchst du? z.B. 200 g Spaghetti";
    inputText.autocomplete = "off";

    var inputQuantity = el("input", "shop-add-quantity");
    inputQuantity.type = "text";
    inputQuantity.name = "quantity";
    inputQuantity.placeholder = "Menge";
    inputQuantity.autocomplete = "off";

    var btn = el("button", "btn");
    btn.type = "submit";
    btn.textContent = "Hinzufügen";

    form.appendChild(inputText);
    form.appendChild(inputQuantity);
    form.appendChild(btn);

    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var text = inputText.value.trim();
      if (!text) {
        inputText.focus();
        return;
      }
      var quantity = inputQuantity.value.trim();
      addItem(text, quantity);
      inputText.value = "";
      inputQuantity.value = "";
      inputText.focus();
    });

    return form;
  }

  // Empty state exactly like the server (.empty + 🧺).
  function renderEmpty() {
    var wrap = el("div", "empty");
    var emoji = el("p", "empty-emoji");
    emoji.textContent = "🧺";
    wrap.appendChild(emoji);
    var p = el("p");
    p.textContent =
      "Die Einkaufsliste ist leer. Trag oben etwas ein oder schick die " +
      "Zutaten eines Rezepts hierher.";
    wrap.appendChild(p);
    return wrap;
  }

  // --- Mutations -------------------------------------------------------------
  // Pattern everywhere: write localStorage -> re-render -> trigger sync().

  function addItem(text, quantity) {
    var items = loadItems();
    var now = nowIso();
    items.push({
      id: crypto.randomUUID(),
      text: text,
      quantity: quantity || "",
      checked: false,
      source: null,
      created_at: now,
      updated_at: now,
      deleted: false,
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
        items[i].updated_at = nowIso(items[i].updated_at);
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
        items[i].deleted = true;
        items[i].updated_at = nowIso(items[i].updated_at);
        break;
      }
    }
    saveItems(items);
    render();
    sync();
  }

  function clearDone() {
    var items = loadItems();
    items.forEach(function (it) {
      if (it.checked && !it.deleted) {
        it.deleted = true;
        it.updated_at = nowIso(it.updated_at);
      }
    });
    saveItems(items);
    render();
    sync();
  }

  // --- Sync ------------------------------------------------------------------
  // Full state: upload the complete local state (incl. tombstones), take over
  // the merged response. Fault-tolerant: offline / network error -> keep the
  // local state.

  var syncing = false;
  var pending = false;

  // Merge the server response into the CURRENT local state -- same rule as the
  // server (per id the larger updated_at wins). This way a sync response does
  // not overwrite local changes made during the in-flight request (otherwise
  // fast clicks would be lost).
  function mergeInto(local, incoming) {
    var byId = {};
    local.forEach(function (it) { byId[it.id] = it; });
    incoming.forEach(function (rem) {
      var cur = byId[rem.id];
      if (!cur || isNewer(rem.updated_at, cur.updated_at)) {
        byId[rem.id] = rem;
      }
    });
    return Object.keys(byId).map(function (k) { return byId[k]; });
  }

  function sync() {
    if (!navigator.onLine) return;
    if (syncing) { pending = true; return; } // changed during sync -> follow up
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
        // merge against the CURRENT state, do not blindly overwrite.
        saveItems(mergeInto(loadItems(), incoming));
        render();
      })
      .catch(function () {
        // network/server error -> keep local state, do not render.
      })
      .then(function () {
        syncing = false;
        if (pending) sync(); // follow up on changes made during the sync
      });
  }

  // --- Start -----------------------------------------------------------------

  function init() {
    serverEl = document.getElementById("shop-server");
    clientEl = document.getElementById("shop-client");
    if (!clientEl) return; // No container -> do nothing (server fallback stays).

    // JS active: hide the server part, show the client part.
    if (serverEl) serverEl.hidden = true;
    clientEl.hidden = false;

    render();
    sync(); // first run (even empty) fills the local state from the server.
  }

  document.addEventListener("DOMContentLoaded", init);
  window.addEventListener("online", sync);
})();
