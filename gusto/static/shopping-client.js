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
  var sourceReferences = {};
  var memoryItems = [];
  var storageWriteFailed = false;
  var rejectedPayload = null;
  var syncRejection = "";
  var generation = "initial";
  var recoveryKey = "gusto.shopping.before-restore";
  var previousLists = [];
  var recoveryInMemory = false;
  try {
    var savedRecovery = JSON.parse(localStorage.getItem(recoveryKey) || "[]");
    if (Array.isArray(savedRecovery)) previousLists = savedRecovery;
  } catch (e) { /* A blocked browser store still permits current-page recovery. */ }

  // --- localStorage binding --------------------------------------------------

  // Always returns an array of items (incl. tombstones). Robust against a
  // missing / broken store.
  function loadItems() {
    // A quota/privacy failure must not replace newer, unsaved changes with
    // an old (or empty) disk copy. Keep using memory until a write succeeds.
    if (storageWriteFailed) return memoryItems;
    try {
      var raw = localStorage.getItem(STORAGE_KEY);
      var data = raw ? JSON.parse(raw) : null;
      memoryItems = data && Array.isArray(data.items) ? data.items : [];
      generation = data && typeof data.generation === "string" ? data.generation : "initial";
    } catch (e) {
      // Keep the last known state when storage is blocked or corrupt.
    }
    return memoryItems;
  }

  function saveItems(items) {
    memoryItems = items;
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ items: items, generation: generation }));
      storageWriteFailed = false;
    } catch (e) {
      // Current-page edits still render and sync even without persistence.
      storageWriteFailed = true;
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

  // Parse legacy ISO calendar/week dates as well as the extended form emitted
  // by Gusto. Date.parse alone rejects basic and week dates accepted by Core.
  // Return milliseconds plus the remaining microseconds for rounding up.
  function parseSyncInstant(value) {
    var dateMatch = /^(\d{4})(?:-(\d\d)-(\d\d)|(\d\d)(\d\d)|-W(\d\d)(?:-(\d))?|W(\d\d)(\d)?)(?:[\s\S](.+))?$/.exec(value || "");
    if (!dateMatch) return null;
    var year = Number(dateMatch[1]);
    if (!year) return null;
    var date = new Date(0);
    date.setUTCHours(0, 0, 0, 0);
    if (dateMatch[6] || dateMatch[8]) {
      var week = Number(dateMatch[6] || dateMatch[8]);
      var weekday = Number(dateMatch[7] || dateMatch[9] || 1);
      if (week < 1 || week > 53 || weekday < 1 || weekday > 7) return null;
      date.setUTCFullYear(year, 0, 4);
      var mondayOffset = (date.getUTCDay() + 6) % 7;
      date.setUTCDate(date.getUTCDate() - mondayOffset + (week - 1) * 7 + weekday - 1);
      var thursday = new Date(date.getTime());
      thursday.setUTCDate(thursday.getUTCDate() + 4 - weekday);
      if (thursday.getUTCFullYear() !== year) return null;
    } else {
      var month = Number(dateMatch[2] || dateMatch[4]);
      var day = Number(dateMatch[3] || dateMatch[5]);
      date.setUTCFullYear(year, month - 1, day);
      if (date.getUTCFullYear() !== year || date.getUTCMonth() !== month - 1 ||
          date.getUTCDate() !== day) return null;
    }
    var time = dateMatch[10];
    if (!time) return { ms: date.getTime(), micros: 0 };
    var parts = /^(\d\d)(?::?(\d\d))?(?::?(\d\d))?(?:[.,](\d+))?(Z|[+-]\d\d(?::?\d\d(?::?\d\d(?:[.,]\d+)?)?)?)?$/.exec(time);
    if (!parts) return null;
    if ((parts[4] && !parts[3]) || (parts[3] &&
        !/^(?:\d{6}|\d\d:\d\d:\d\d)/.test(time))) return null;
    var hours = Number(parts[1]), minutes = Number(parts[2] || 0), seconds = Number(parts[3] || 0);
    if (hours > 23 || minutes > 59 || seconds > 59) return null;
    var fraction = Number(((parts[4] || "") + "000000").slice(0, 6));
    var micros = ((hours * 60 + minutes) * 60 + seconds) * 1000000 + fraction;
    var zone = parts[5];
    if (zone && zone !== "Z") {
      var offset = /^(\d\d)(?::?(\d\d))?(?::?(\d\d))?(?:[.,](\d+))?$/.exec(zone.slice(1));
      if ((offset[4] && !offset[3]) || (offset[3] &&
          !/^(?:\d{6}|\d\d:\d\d:\d\d)/.test(zone.slice(1)))) return null;
      var offsetHours = Number(offset[1]), offsetMinutes = Number(offset[2] || 0);
      var offsetSeconds = Number(offset[3] || 0);
      var offsetFraction = Number(((offset[4] || "") + "000000").slice(0, 6));
      var offsetWholeSeconds = (offsetHours * 60 + offsetMinutes) * 60 + offsetSeconds;
      // Python normalizes oversized offset minutes/seconds, but ignores a
      // fractional offset when the whole offset is zero.
      var offsetMicros = offsetWholeSeconds * 1000000 + (offsetWholeSeconds ? offsetFraction : 0);
      if (offsetMicros >= 86400 * 1000000) return null;
      micros += zone[0] === "+" ? -offsetMicros : offsetMicros;
    }
    var ms = date.getTime() + Math.floor(micros / 1000);
    var min = new Date("0001-01-01T00:00:00.000Z").getTime();
    var max = new Date("9999-12-31T23:59:59.999Z").getTime();
    if (ms < min || ms > max) return null;
    return { ms: ms, micros: ((micros % 1000) + 1000) % 1000 };
  }

  // Server-compatible timestamp: UTC with milliseconds and a literal Z.
  // For repeated changes to one item, always advance by at least 1 ms so an
  // immediate add-then-toggle cannot produce an ambiguous sync version.
  function nowIso(after) {
    var now = Date.now();
    var previous = parseSyncInstant(after);
    if (previous) {
      var minimum = previous.ms + (previous.micros ? 2 : 1);
      if (now < minimum) now = minimum;
    }
    return new Date(now).toISOString();
  }

  // New unique item id. crypto.randomUUID exists only in secure contexts;
  // over plain HTTP (LAN) fall back to a UUID-v4-shaped id from
  // crypto.getRandomValues, then to Math.random + time. Ids are not secrets,
  // uniqueness is all that matters.
  function newId() {
    if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
      return crypto.randomUUID();
    }
    if (typeof crypto !== "undefined" && typeof crypto.getRandomValues === "function") {
      var bytes = new Uint8Array(16);
      crypto.getRandomValues(bytes);
      bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
      bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 10
      var hex = "";
      for (var i = 0; i < bytes.length; i++) {
        hex += (bytes[i] < 16 ? "0" : "") + bytes[i].toString(16);
      }
      return hex.slice(0, 8) + "-" + hex.slice(8, 12) + "-" +
        hex.slice(12, 16) + "-" + hex.slice(16, 20) + "-" + hex.slice(20);
    }
    // No WebCrypto at all: time + Math.random is unique enough for an id here.
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, function (c) {
      var r = (Math.random() * 16) | 0;
      var v = c === "x" ? r : (r & 0x3) | 0x8;
      return v.toString(16);
    }) + "-" + Date.now().toString(16);
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
    li.dataset.itemId = item.id;

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
      var reference = sourceReferences[item.source];
      var source = el(reference ? "a" : "span",
        "shop-source" + (reference ? "" : " is-unresolved"));
      if (reference) source.href = reference.url;
      source.textContent = reference
        ? "aus „" + reference.title + "“" + (reference.archived ? " (archiviert)" : "")
        : "aus „" + item.source + "“ (nicht gefunden)";
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

  function rememberListFocus() {
    var active = document.activeElement;
    if (!clientEl.contains(active) || active.closest(".shop-add-panel")) return null;
    var row = active.closest(".shop-item");
    if (!row) return { index: 0, control: ".shop-box" };
    return {
      id: row.dataset.itemId,
      index: Array.from(clientEl.querySelectorAll(".shop-item")).indexOf(row),
      control: active.classList.contains("shop-x") ? ".shop-x"
        : active.classList.contains("shop-favorite-trigger") ? ".shop-favorite-trigger"
        : ".shop-box",
    };
  }

  function restoreListFocus(saved) {
    if (!saved) return;
    var rows = Array.from(clientEl.querySelectorAll(".shop-item"));
    var row = rows.find(function (node) { return node.dataset.itemId === saved.id; });
    row = row || rows[Math.min(saved.index, rows.length - 1)];
    var target = row ? row.querySelector(saved.control)
      : clientEl.querySelector(".shop-add-panel summary");
    if (target) target.focus({ preventScroll: true });
  }

  // Render from localStorage: drop deleted, group open/done, sort by created_at
  // ascending.
  function render() {
    if (!clientEl) return;
    var savedFocus = rememberListFocus();

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

    // Keep the add panel across re-renders: replacing it would destroy a
    // half-typed input and its focus on every sync/SSE re-render. The panel
    // is created once and must never be detached -- removing even briefly a
    // focused node moves focus to <body> and it does not come back. Only the
    // children after the panel are rebuilt.
    var addPanel = clientEl.querySelector("details.shop-add-panel");
    if (!addPanel) {
        clientEl.textContent = ""; // first render only: drop leftover markup
        addPanel = renderAddPanel();
        clientEl.appendChild(addPanel);
    }
    while (clientEl.lastChild && clientEl.lastChild !== addPanel) {
        clientEl.removeChild(clientEl.lastChild);
    }

    if (previousLists.length) {
      var restored = el("div", "error");
      restored.id = "shop-restore-notice";
      restored.setAttribute("role", "status");
      var explanation = el("p");
      explanation.textContent = "Eine Sicherung wurde wiederhergestellt. Deine bisherige lokale "
        + "Einkaufsliste wurde getrennt aufbewahrt und nicht erneut hochgeladen."
        + (recoveryInMemory ? " Lade sie vor dem Schließen dieses Tabs herunter." : "");
      restored.appendChild(explanation);
      var download = el("button", "btn");
      download.type = "button";
      download.textContent = "Vorherige lokale Listen herunterladen";
      download.addEventListener("click", function () {
        var url = URL.createObjectURL(new Blob([JSON.stringify(previousLists, null, 2)],
          { type: "application/json" }));
        var link = el("a");
        link.href = url;
        link.download = "gusto-lokale-listen-vor-wiederherstellung.json";
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
      });
      restored.appendChild(download);
      clientEl.appendChild(restored);
    }

    if (syncRejection) {
      var notice = el("div", "error");
      notice.id = "shop-sync-error";
      notice.setAttribute("role", "alert");
      notice.textContent = "Abgleich angehalten: " + syncRejection
        + " Deine Einträge bleiben auf diesem Gerät. Entferne den betroffenen Eintrag"
        + " und füge ihn bei Bedarf neu hinzu. Bei einer Änderung versuchen wir es erneut.";
      clientEl.appendChild(notice);
    }

    if (openItems.length === 0 && doneItems.length === 0) {
      clientEl.appendChild(renderEmpty());
      restoreListFocus(savedFocus);
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
    restoreListFocus(savedFocus);
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
    inputText.setAttribute("aria-label", "Artikel");
    inputText.placeholder = "Was brauchst du? z.B. 200 g Spaghetti";
    inputText.autocomplete = "off";

    var inputQuantity = el("input", "shop-add-quantity");
    inputQuantity.type = "text";
    inputQuantity.name = "quantity";
    inputQuantity.setAttribute("aria-label", "Menge (optional)");
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

  var favoriteBackground = [];
  var favoriteReturnItem = null;

  function openFavoriteSheet(text) {
    if (!favoriteSheet || !favoriteSheetContent) return;
    var wasOpen = !favoriteSheet.hidden;
    if (!wasOpen) {
      favoriteReturnFocus = document.activeElement;
      favoriteReturnItem = rememberListFocus();
      favoriteBackground = Array.from(document.querySelectorAll(
        ".masthead, .footer, .skip-link, main > :not(#favorite-sheet)"
      )).filter(function (node) { return !node.inert; });
      favoriteBackground.forEach(function (node) { node.inert = true; });
    }
    // Keep a draft in an open assignment form when another device changes
    // the favorites catalog. Reopening the sheet picks up the new choices.
    if (wasOpen && favoriteSheetContent.querySelector('form[data-dirty="true"]')) return;
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
    if (closeButton && (!wasOpen || !favoriteSheet.contains(document.activeElement))) {
      closeButton.focus();
    }
  }

  function closeFavoriteSheet() {
    if (!favoriteSheet) return;
    favoriteSheet.hidden = true;
    document.body.classList.remove("has-favorite-sheet");
    favoriteBackground.forEach(function (node) { node.inert = false; });
    favoriteBackground = [];
    openFavoriteText = "";
    if (favoriteReturnFocus && favoriteReturnFocus.isConnected) favoriteReturnFocus.focus();
    else restoreListFocus(favoriteReturnItem || { index: 0, control: ".shop-favorite-trigger" });
    favoriteReturnFocus = null;
    favoriteReturnItem = null;
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

  var referenceRequest = 0;
  function refreshSourceReferences() {
    var request = ++referenceRequest;
    // Source titles/archival links are presentation metadata in the HTML.
    // Refresh that map without navigating away from an unfinished add form.
    fetch("/shopping")
      .then(function (response) {
        if (!response.ok) throw new Error("source references unavailable");
        return response.text();
      })
      .then(function (markup) {
        if (request !== referenceRequest) return;
        var doc = new DOMParser().parseFromString(markup, "text/html");
        var node = doc.getElementById("shop-recipe-references");
        if (!node) return;
        var references = JSON.parse(node.textContent);
        if (!references || typeof references !== "object" || Array.isArray(references)) return;
        sourceReferences = references;
        render();
      })
      .catch(function () { /* Keep the last known metadata while offline. */ });
  }

  // --- Mutations -------------------------------------------------------------
  // Pattern everywhere: write localStorage -> re-render -> trigger sync().

  function addItem(text, quantity) {
    var items = loadItems();
    var now = nowIso();
    items.push({
      id: newId(),
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
  // Bounded retry for a failed sync (exponential backoff with a little
  // jitter so parallel tabs do not retry in lockstep). Success resets the
  // budget; offline pauses it because the "online" event re-syncs.
  var RETRY_BASE_MS = 2000;
  var RETRY_MAX_MS = 60000;
  var RETRY_MAX_ATTEMPTS = 5;
  var retryAttempts = 0;
  var retryTimer = null;

  function resetRetry() {
    clearTimeout(retryTimer);
    retryTimer = null;
    retryAttempts = 0;
  }

  function scheduleRetry() {
    clearTimeout(retryTimer);
    if (!navigator.onLine || retryAttempts >= RETRY_MAX_ATTEMPTS) return;
    var delay = Math.min(
      RETRY_BASE_MS * Math.pow(2, retryAttempts) + Math.floor(Math.random() * 500),
      RETRY_MAX_MS
    );
    retryAttempts += 1;
    retryTimer = setTimeout(function () {
      retryTimer = null;
      sync();
    }, delay);
  }

  // The server has already compared the submitted versions using its exact
  // timestamp rules. Accept its result unless this device changed that item
  // during the request; those edits stay local and need another exchange.
  function mergeInto(local, incoming, submitted) {
    var byId = Object.create(null);
    var sentById = Object.create(null);
    var changedDuringRequest = false;
    submitted.forEach(function (it) { sentById[it.id] = JSON.stringify(it); });
    local.forEach(function (it) { byId[it.id] = it; });
    incoming.forEach(function (rem) {
      var cur = byId[rem.id];
      if (!cur || (sentById[rem.id] !== undefined &&
                   JSON.stringify(cur) === sentById[rem.id])) {
        byId[rem.id] = rem;
      } else {
        changedDuringRequest = true;
      }
    });
    local.forEach(function (it) {
      if (sentById[it.id] === undefined || JSON.stringify(it) !== sentById[it.id]) {
        changedDuringRequest = true;
      }
    });
    return {
      items: Object.keys(byId).map(function (k) { return byId[k]; }),
      changedDuringRequest: changedDuringRequest,
    };
  }

  function sync() {
    if (!navigator.onLine) return;
    if (syncing) { pending = true; return; } // changed during sync -> follow up
    var currentItems = loadItems();
    var payload = JSON.stringify({ items: currentItems, generation: generation });
    var submittedGeneration = generation;
    // A rejected full state cannot succeed unchanged. Keep it locally and
    // let an actual list change unblock sync, including an in-flight edit.
    if (payload === rejectedPayload) return;
    var submitted = JSON.parse(payload).items;
    syncing = true;
    pending = false;

    fetch(SYNC_URL, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: payload,
    })
      .then(function (res) {
        if (res.status === 400) {
          return res.json().catch(function () { return {}; }).then(function (data) {
            var error = new Error(data && typeof data.detail === "string"
              ? data.detail : "Ein Eintrag konnte nicht übernommen werden.");
            error.syncRejected = true;
            throw error;
          });
        }
        if (!res.ok) throw new Error("sync failed: " + res.status);
        return res.json();
      })
      .then(function (data) {
        resetRetry();
        rejectedPayload = null;
        syncRejection = "";
        var incoming = data && Array.isArray(data.items) ? data.items : [];
        var latestItems = loadItems();
        if (generation !== submittedGeneration) {
          // Another tab adopted a restored store during this request. Any
          // response for the former generation is obsolete, even a success.
          pending = true;
          return;
        }
        if (data.reset === true) {
          var previousItems = latestItems;
          if (previousItems.length) {
            previousLists.push({ generation: generation, items: previousItems });
            try {
              localStorage.setItem(recoveryKey, JSON.stringify(previousLists));
              recoveryInMemory = false;
            } catch (e) { recoveryInMemory = true; }
          }
          generation = data.generation;
          saveItems(incoming);
          pending = false;
          render();
          return;
        }
        var merged = mergeInto(latestItems, incoming, submitted);
        if (typeof data.generation === "string") generation = data.generation;
        saveItems(merged.items);
        if (merged.changedDuringRequest) pending = true;
        render();
      })
      .catch(function (error) {
        if (error.syncRejected) {
          resetRetry();
          rejectedPayload = payload;
          syncRejection = error.message;
          render();
          return;
        }
        // Network/server error -> keep the local state, do not render, and
        // retry with backoff so an offline change is not left unsynchronized
        // until an unrelated event happens to fire.
        scheduleRetry();
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

    var referencesEl = document.getElementById("shop-recipe-references");
    if (referencesEl) {
      try {
        var parsedReferences = JSON.parse(referencesEl.textContent);
        if (parsedReferences && typeof parsedReferences === "object") {
          sourceReferences = parsedReferences;
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
      if (!favoriteSheet || favoriteSheet.hidden) return;
      if (event.key === "Escape") {
        event.preventDefault();
        closeFavoriteSheet();
      }
      if (event.key === "Tab") {
        var controls = Array.from(favoriteSheet.querySelectorAll(
          '.favorite-sheet-panel button, .favorite-sheet-panel a[href], ' +
          '.favorite-sheet-panel input, .favorite-sheet-panel select, .favorite-sheet-panel textarea'
        )).filter(function (node) { return !node.disabled && node.getClientRects().length; });
        var first = controls[0];
        var last = controls[controls.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
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
    resetRetry(); // fresh connection -> fresh retry budget
    sync();
    refreshFavoriteNeeds();
    refreshSourceReferences();
  });
  document.addEventListener("gusto:change", function (event) {
    var resources = event.detail && Array.isArray(event.detail.resources)
      ? event.detail.resources
      : [];
    if (resources.indexOf("shopping") !== -1) sync();
    if (resources.indexOf("favorites") !== -1) refreshFavoriteNeeds();
    if (resources.indexOf("catalog") !== -1) refreshSourceReferences();
  });
})();
