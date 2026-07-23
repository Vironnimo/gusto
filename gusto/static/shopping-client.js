// shopping-client.js — offline-capable client for the Gusto shopping list.
//
// Renders the list from localStorage, mutates optimistically offline and syncs
// in the background with the durable Gusto server via a full-state POST to
// /api/shopping/sync. Uses exactly the same CSS classes as the server-rendered
// version in shopping.html — no extra CSS needed.
//
// See docs/sync-kontrakt.md, section "Client store + behavior (2C)".

(function () {
  "use strict";

  var STORAGE_KEY = "gusto.shopping";
  var SYNC_URL = "/api/shopping/sync";
  var FAVORITES_KEY = "gusto.favorites";
  var FAVORITES_URL = "/api/favorites";
  var favoriteNeeds = [];
  var sourceTitles = {};

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

  function loadFavoriteNeeds() {
    try {
      var raw = localStorage.getItem(FAVORITES_KEY);
      if (!raw) return [];
      var data = JSON.parse(raw);
      if (data && Array.isArray(data.needs)) return data.needs;
    } catch (e) {
      // Missing/broken cache -> recommendations fill on the next online load.
    }
    return [];
  }

  function saveFavoriteNeeds(needs) {
    try {
      localStorage.setItem(FAVORITES_KEY, JSON.stringify({ needs: needs }));
    } catch (e) {
      // Recommendations remain usable in memory if storage is unavailable.
    }
  }

  function normalizeShoppingText(value) {
    return String(value || "").trim().replace(/\s+/g, " ").toLowerCase().replace(/ß/g, "ss");
  }

  function findFavoriteNeed(text) {
    var normalized = normalizeShoppingText(text);
    for (var i = 0; i < favoriteNeeds.length; i++) {
      var need = favoriteNeeds[i];
      var labels = [need.name].concat(need.aliases || []);
      for (var j = 0; j < labels.length; j++) {
        if (normalizeShoppingText(labels[j]) === normalized) return need;
      }
    }
    return null;
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

    // Body: tappable recommendation entry + optional quantity/source metadata.
    var body = el("div", "shop-body");
    var trigger = el("button", "shop-favorite-trigger");
    trigger.type = "button";
    var text = el("span", "shop-text");
    text.textContent = item.text;
    trigger.appendChild(text);

    var need = findFavoriteNeed(item.text);
    var hint = el("span", "shop-favorite-hint");
    if (need && (need.products || []).length > 0) {
      var count = need.products.length;
      hint.textContent = "♥ " + count + " " + (count === 1 ? "Favorit" : "Favoriten");
    } else if (need) {
      hint.textContent = "♡ Produkte ergänzen";
    } else {
      hint.textContent = "♡ Lieblingsprodukt";
    }
    trigger.appendChild(hint);
    trigger.addEventListener("click", function () {
      openFavoriteSheet(item.text);
    });
    body.appendChild(trigger);

    var meta = el("span", "shop-meta");

    if (item.quantity) {
      var quantity = el("span", "shop-quantity");
      quantity.textContent = item.quantity;
      meta.appendChild(quantity);
    }

    if (item.source) {
      var source = el("a", "shop-source");
      source.href = "/recipe/" + item.source;
      source.textContent = "aus „" + (sourceTitles[item.source] || item.source) + "“";
      meta.appendChild(source);
    }
    if (meta.childNodes.length) body.appendChild(meta);

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

    // Compact add panel (same structure as the server-rendered fallback).
    clientEl.appendChild(renderAddPanel());

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

      var removeDoneForm = el("div", "shop-remove-done");
      var removeDoneBtn = el("button", "btn-text");
      removeDoneBtn.type = "button";
      removeDoneBtn.textContent = "Erledigte entfernen";
      removeDoneBtn.addEventListener("click", function () {
        removeDone();
      });
      removeDoneForm.appendChild(removeDoneBtn);
      grpDone.appendChild(removeDoneForm);

      board.appendChild(grpDone);
    }

    board.appendChild(renderClearAll());
    clientEl.appendChild(board);
  }

  function renderAddPanel() {
    var panel = el("details", "shop-add-panel");
    var summary = el("summary");
    var label = el("span");
    label.textContent = "Artikel hinzufügen";
    var plus = el("span");
    plus.textContent = "＋";
    plus.setAttribute("aria-hidden", "true");
    summary.appendChild(label);
    summary.appendChild(plus);
    panel.appendChild(summary);
    panel.appendChild(renderAddForm());
    return panel;
  }

  function renderClearAll() {
    var panel = el("details", "shop-clear-all");
    var summary = el("summary");
    summary.textContent = "Einkaufsliste leeren";
    panel.appendChild(summary);

    var body = el("div", "shop-clear-all-body");
    var explanation = el("p");
    explanation.textContent = "Entfernt alle offenen und erledigten Einträge. Diese Aktion lässt sich in Gusto nicht rückgängig machen.";
    body.appendChild(explanation);

    var button = el("button", "btn-text");
    button.type = "button";
    button.textContent = "Alles entfernen";
    button.addEventListener("click", function () {
      if (window.confirm("Wirklich alle Einträge von der Einkaufsliste entfernen?")) {
        clearAll();
      }
    });
    body.appendChild(button);
    panel.appendChild(body);
    return panel;
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

  // --- Preferred-product sheet ---------------------------------------------

  var favoriteSheet = null;
  var favoriteSheetContent = null;
  var openFavoriteText = "";
  var favoriteReturnFocus = null;

  function hiddenInput(name, value) {
    var input = el("input");
    input.type = "hidden";
    input.name = name;
    input.value = value;
    return input;
  }

  function labeledControl(labelText, control) {
    var label = el("label");
    label.appendChild(document.createTextNode(labelText));
    label.appendChild(control);
    return label;
  }

  function renderFavoriteProduct(product, position) {
    var card = el("article", "favorite-product-card");
    var rank = el("span", "favorite-product-rank");
    rank.textContent = String(position);
    card.appendChild(rank);

    if (product.image_url) {
      var image = el("img");
      image.src = product.image_url;
      image.alt = product.name;
      card.appendChild(image);
    } else {
      var placeholder = el("div", "favorite-product-placeholder");
      placeholder.textContent = (product.name || "?").slice(0, 1);
      placeholder.setAttribute("aria-hidden", "true");
      card.appendChild(placeholder);
    }

    var body = el("div");
    if (position === 1) {
      var kicker = el("p", "favorite-product-kicker");
      kicker.textContent = "Am liebsten";
      body.appendChild(kicker);
    }
    var name = el("h3");
    name.textContent = product.name;
    body.appendChild(name);
    if (product.brand) {
      var brand = el("p", "favorite-product-brand");
      brand.textContent = product.brand;
      body.appendChild(brand);
    }
    if (product.store) {
      var store = el("p", "favorite-product-store");
      store.textContent = "bei " + product.store;
      body.appendChild(store);
    }
    if (product.note) {
      var note = el("p", "favorite-product-note");
      note.textContent = "„" + product.note + "“";
      body.appendChild(note);
    }
    card.appendChild(body);
    return card;
  }

  function renderFavoriteSetup(text) {
    var lede = el("p", "favorite-sheet-lede");
    lede.textContent =
      "Ordne diese Formulierung einmalig zu. Danach erkennt Gusto sie automatisch.";
    favoriteSheetContent.appendChild(lede);

    if (!navigator.onLine) {
      var offline = el("p", "favorite-offline-note");
      offline.textContent = "Zum Zuordnen oder Anlegen bitte kurz wieder online gehen.";
      favoriteSheetContent.appendChild(offline);
      return;
    }

    var grid = el("div", "favorite-setup-grid");
    if (favoriteNeeds.length > 0) {
      var assign = el("form", "favorite-form favorite-setup-card");
      assign.method = "post";
      assign.action = "/favorites/assign";
      var assignTitle = el("h3");
      assignTitle.textContent = "Vorhandenem Bedarf zuordnen";
      assign.appendChild(assignTitle);
      assign.appendChild(hiddenInput("alias", text));
      assign.appendChild(hiddenInput("return_to", "/shopping"));
      var select = el("select");
      select.name = "need_id";
      favoriteNeeds.forEach(function (need) {
        var option = el("option");
        option.value = need.id;
        option.textContent = need.name;
        select.appendChild(option);
      });
      assign.appendChild(labeledControl("Einkaufsbedarf", select));
      var assignButton = el("button", "btn");
      assignButton.type = "submit";
      assignButton.textContent = "Zuordnen";
      assign.appendChild(assignButton);
      grid.appendChild(assign);
    }

    var create = el("form", "favorite-form favorite-setup-card");
    create.method = "post";
    create.action = "/favorites/add";
    var createTitle = el("h3");
    createTitle.textContent = "Neu anlegen";
    create.appendChild(createTitle);
    create.appendChild(hiddenInput("alias", text));
    create.appendChild(hiddenInput("return_to", "/shopping"));
    var nameInput = el("input");
    nameInput.name = "name";
    nameInput.value = text;
    nameInput.required = true;
    create.appendChild(labeledControl("Name des Einkaufsbedarfs", nameInput));
    var createButton = el("button", "btn-ghost");
    createButton.type = "submit";
    createButton.textContent = "Neu anlegen";
    create.appendChild(createButton);
    grid.appendChild(create);
    favoriteSheetContent.appendChild(grid);
  }

  function openFavoriteSheet(text) {
    if (!favoriteSheet || !favoriteSheetContent) return;
    favoriteReturnFocus = document.activeElement;
    openFavoriteText = text;
    favoriteSheetContent.textContent = "";
    var need = findFavoriteNeed(text);

    var eyebrow = el("p", "eyebrow");
    eyebrow.textContent = need ? "Unsere Wahl für" : "Noch nicht zugeordnet";
    favoriteSheetContent.appendChild(eyebrow);
    var title = el("h2");
    title.id = "favorite-sheet-title";
    title.textContent = need ? need.name : text;
    favoriteSheetContent.appendChild(title);

    if (need) {
      var products = need.products || [];
      if (products.length > 0) {
        var list = el("div", "favorite-product-list");
        products.forEach(function (product, index) {
          list.appendChild(renderFavoriteProduct(product, index + 1));
        });
        favoriteSheetContent.appendChild(list);
      } else {
        var empty = el("div", "favorite-empty-note");
        var heart = el("span");
        heart.textContent = "♡";
        var message = el("p");
        message.textContent = "Für diesen Einkaufsbedarf sind noch keine Produkte hinterlegt.";
        empty.appendChild(heart);
        empty.appendChild(message);
        favoriteSheetContent.appendChild(empty);
      }
      var manage = el("a", "btn-ghost favorite-manage-link");
      manage.href = "/favorites/" + need.id;
      manage.textContent = "Produkte bearbeiten";
      favoriteSheetContent.appendChild(manage);
    } else {
      renderFavoriteSetup(text);
    }

    favoriteSheet.hidden = false;
    document.body.classList.add("has-favorite-sheet");
    var closeButton = favoriteSheet.querySelector(".favorite-sheet-close");
    if (closeButton) closeButton.focus();
  }

  function closeFavoriteSheet() {
    if (!favoriteSheet) return;
    favoriteSheet.hidden = true;
    document.body.classList.remove("has-favorite-sheet");
    openFavoriteText = "";
    if (favoriteReturnFocus && favoriteReturnFocus.focus) favoriteReturnFocus.focus();
    favoriteReturnFocus = null;
  }

  function cacheFavoriteImages(needs) {
    needs.forEach(function (need) {
      (need.products || []).forEach(function (product) {
        if (product.image_url) fetch(product.image_url).catch(function () {});
      });
    });
  }

  function refreshFavoriteNeeds() {
    if (!navigator.onLine) return;
    fetch(FAVORITES_URL)
      .then(function (response) {
        if (!response.ok) throw new Error("favorites failed: " + response.status);
        return response.json();
      })
      .then(function (data) {
        favoriteNeeds = data && Array.isArray(data.needs) ? data.needs : [];
        saveFavoriteNeeds(favoriteNeeds);
        cacheFavoriteImages(favoriteNeeds);
        render();
        if (openFavoriteText) openFavoriteSheet(openFavoriteText);
      })
      .catch(function () {
        // Offline/server failure -> keep the last catalog in localStorage.
      });
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

  function removeDone() {
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

  function clearAll() {
    var items = loadItems();
    items.forEach(function (it) {
      if (!it.deleted) {
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

    var titlesEl = document.getElementById("shop-recipe-titles");
    if (titlesEl) {
      try {
        var parsedTitles = JSON.parse(titlesEl.textContent);
        if (parsedTitles && typeof parsedTitles === "object") {
          sourceTitles = parsedTitles;
        }
      } catch (e) {
        // A broken presentation map must not block the offline list.
      }
    }
    favoriteNeeds = loadFavoriteNeeds();
    favoriteSheet = document.getElementById("favorite-sheet");
    favoriteSheetContent = document.getElementById("favorite-sheet-content");
    if (favoriteSheet) {
      favoriteSheet.querySelectorAll("[data-favorite-close]").forEach(function (button) {
        button.addEventListener("click", closeFavoriteSheet);
      });
    }
    document.addEventListener("keydown", function (event) {
      if (event.key === "Escape" && favoriteSheet && !favoriteSheet.hidden) {
        closeFavoriteSheet();
      }
    });

    // JS active: hide the server part, show the client part.
    if (serverEl) serverEl.hidden = true;
    clientEl.hidden = false;

    render();
    sync(); // first run (even empty) fills the local state from the server.
    refreshFavoriteNeeds();
  }

  document.addEventListener("DOMContentLoaded", init);
  window.addEventListener("online", function () {
    sync();
    refreshFavoriteNeeds();
  });
})();
