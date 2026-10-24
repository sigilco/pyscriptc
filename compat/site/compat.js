/* pyscriptc compat site. Plain DOM, no framework, no third-party script.
   Everything renders from data/compat.json (the schema lives in
   scripts/compat_schema.py next to the page source in the repository).
   The base path is baked in at build time as window.__COMPAT_BASE__. */
(function () {
  "use strict";

  var BASE = (window.__COMPAT_BASE !== undefined ? window.__COMPAT_BASE : "/compat/")
    .replace(/\/*$/, "/");
  var DATA_URL = BASE + "data/compat.json";

  var STATUS_EMOJI = { full: "\u{1F7E2}", partial: "\u{1F7E1}", none: "\u{1F534}", unknown: "\u2014" };
  var STATUS_TEXT = { full: "full", partial: "partial", none: "none", unknown: "unknown" };

  // Per-section table config: thead, row builder, searchable text.
  var SECTIONS = {
    language: {
      table: "language-table",
      columns: ["Group", "Probes", "Pass", "Fence", "Other", "Weighted pass", "Fences"],
      row: function (r) {
        var outcomes = r.outcomes || {};
        var other = (outcomes["wrong-output"] || 0) + (outcomes.crash || 0) +
          (outcomes.timeout || 0) + (outcomes["build-error"] || 0) + (outcomes.excluded || 0);
        return [
          cell("td", r.label),
          cellNum(outcomes.pass || 0),
          cellNum(outcomes.fence || 0),
          cellNum(other),
          cellNum(r.weighted_pass),
          cellCodes(r.fence_codes)
        ];
      },
      search: function (r) { return r.label + " " + r.id; }
    },
    builtins: {
      table: "builtins-table",
      columns: ["Status", "Builtins", "Not supported", "Fences", "Note"],
      row: statusRow,
      search: function (r) {
        return r.label + " " + r.names.join(" ") + " " + r.unsupported.join(" ") +
          " " + r.fences.join(" ") + " " + (r.note || "");
      }
    },
    stdlib: {
      table: "stdlib-table",
      columns: ["Status", "Module", "Not supported", "Fences", "Note"],
      row: statusRow,
      search: function (r) {
        return r.label + " " + r.names.join(" ") + " " + r.unsupported.join(" ") +
          " " + r.fences.join(" ") + " " + (r.note || "");
      }
    },
    packages: {
      table: "packages-table",
      columns: ["Status", "Package", "Version", "Fences", "Definitions", "Top fence codes"],
      row: function (r) {
        return [
          statusCell(r.status),
          linkCell(r.name, r.pypi),
          cellNum(r.version),
          cellNum(r.fences),
          cellNum(r.definitions + " (" + r.fenced_definitions + " fenced)"),
          cellCodes(r.codes)
        ];
      },
      search: function (r) {
        return r.name + " " + r.import_name + " " + r.version + " " +
          (r.codes || []).map(function (c) { return c.code; }).join(" ");
      }
    },
    fences: {
      table: "fences-table",
      columns: ["Code", "Title", "Count", "Packages"],
      row: function (r) {
        return [cell("td name", r.code), cell("td", r.title), cellNum(r.count), cellNum(r.packages)];
      },
      search: function (r) { return r.code + " " + r.title; }
    }
  };

  // The five list sections share one generic table engine. State lives per
  // section: the rows, the current filter text and the status chip.
  var state = {};

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function cell(cls, text) { return el("td", cls, text); }

  function cellNum(value) { return cell("td num", value); }

  function cellCodes(codes) {
    var td = el("td");
    (codes || []).forEach(function (c, i) {
      if (i) td.appendChild(document.createTextNode(", "));
      var text = c.count === null || c.count === undefined ? c.code : c.code + " \u00D7" + c.count;
      td.appendChild(el("span", "name", text));
    });
    return td;
  }

  function linkCell(text, href) {
    var td = el("td");
    if (href) {
      var a = el("a", null, text);
      a.href = href;
      a.rel = "noopener";
      td.appendChild(a);
    } else {
      td.textContent = String(text);
    }
    return td;
  }

  function statusRow(r) {
    return [
      statusCell(r.status),
      cell("td name", r.label),
      cell("td", r.unsupported.join(", ")),
      cellCodes(r.fences.map(function (code) { return { code: code, count: null }; })),
      cell("td", r.note || "")
    ];
  }

  function statusCell(status) {
    var td = el("td", "status status-" + status);
    var emoji = el("span", "emoji", STATUS_EMOJI[status] || STATUS_EMOJI.unknown);
    emoji.setAttribute("role", "img");
    emoji.setAttribute("aria-label", STATUS_TEXT[status] || "unknown");
    td.appendChild(emoji);
    // Text fallback next to the emoji, for screen readers and for the
    // people who turn emoji fonts off.
    td.appendChild(el("span", "text", STATUS_TEXT[status] || "unknown"));
    return td;
  }

  function renderTable(key) {
    var cfg = SECTIONS[key];
    var st = state[key];
    var table = document.getElementById(cfg.table);
    table.textContent = "";

    var thead = el("thead");
    var hr = el("tr");
    cfg.columns.forEach(function (c) { hr.appendChild(el("th", null, c)); });
    thead.appendChild(hr);
    table.appendChild(thead);

    var tbody = el("tbody");
    var needle = st.filter.trim().toLowerCase();
    var shown = 0;
    st.rows.forEach(function (r, index) {
      if (st.status !== "all" && r.status !== st.status) return;
      if (needle && cfg.search(r).toLowerCase().indexOf(needle) === -1) return;
      var tr = el("tr");
      tr.id = "row-" + key + "-" + index;
      cfg.row(r).forEach(function (td) { tr.appendChild(td); });
      tbody.appendChild(tr);
      shown += 1;
    });
    table.appendChild(tbody);

    st.count.textContent = shown + " of " + st.rows.length;
    if (!shown) {
      var tr = el("tr");
      var td = el("td", "empty", "nothing matches");
      td.colSpan = cfg.columns.length;
      tr.appendChild(td);
      tbody.appendChild(tr);
    }
  }

  function wireControls(key) {
    var st = state[key];
    var input = document.getElementById(key + "-filter");
    if (input) {
      input.addEventListener("input", function () {
        st.filter = input.value;
        renderTable(key);
      });
    }
    var chips = document.querySelector('.chips[data-for="' + key + '"]');
    if (chips) {
      chips.addEventListener("click", function (event) {
        var button = event.target.closest("button.chip");
        if (!button) return;
        st.status = button.dataset.status;
        chips.querySelectorAll("button.chip").forEach(function (b) {
          b.setAttribute("aria-pressed", b === button ? "true" : "false");
        });
        renderTable(key);
      });
    }
  }

  function countBy(rows) {
    var counts = { full: 0, partial: 0, none: 0, unknown: 0 };
    rows.forEach(function (r) { counts[r.status] = (counts[r.status] || 0) + 1; });
    return counts;
  }

  function countsLine(counts) {
    var parts = [];
    ["full", "partial", "none"].forEach(function (s) {
      if (counts[s]) parts.push(STATUS_EMOJI[s] + " " + counts[s]);
    });
    return parts.join(" \u00B7 ");
  }

  function renderSummary(doc) {
    var s = doc.summary || {};
    function fill(id, html) {
      var card = document.getElementById(id);
      if (!card) return;
      var target = card.querySelector(".counts") || card.querySelector(".big");
      if (target) target.innerHTML = html;
    }
    fill("card-builtins", countsLine(countBy(doc.builtins || [])));
    fill("card-stdlib", countsLine(countBy(doc.stdlib || [])));
    fill("card-packages", countsLine(countBy(doc.packages || [])));
    var weighted = s.weighted_pass;
    if (typeof weighted === "number") {
      var pct = Math.round(weighted * 1000) / 10;
      fill("card-score", pct + "%");
    } else {
      var scoreCard = document.getElementById("card-score");
      if (scoreCard) scoreCard.hidden = true;
    }
    if (typeof s.fences === "number") fill("card-fences", String(s.fences));
  }

  // --- Hero stat: the live supported-% headline, the same number the
  // scorer reports as weighted pass.
  function renderHeroStat(doc) {
    var value = document.getElementById("stat-value");
    var label = document.getElementById("stat-label");
    if (!value) return;
    var weighted = (doc.summary || {}).weighted_pass;
    if (typeof weighted !== "number") {
      value.textContent = "\u2014";
      if (label) label.textContent = "no corpus measurement yet";
      return;
    }
    var pct = Math.round(weighted * 1000) / 10;
    value.textContent = pct + "%";
    var counts = countBy((doc.builtins || []).concat(doc.stdlib || []));
    var total = (doc.builtins || []).length + (doc.stdlib || []).length;
    if (label) {
      label.textContent = counts.full + " of " + total + " builtin and module groups compile native today";
    }
  }

  // --- Search index. One flat list over every listed module, whatever its
  // category: builtins (one entry per builtin name), standard-library
  // modules and PyPI packages.
  function buildSearchIndex(doc) {
    var entries = [];

    (doc.builtins || []).forEach(function (row, index) {
      var names = row.names && row.names.length ? row.names : [row.label];
      names.forEach(function (name) {
        entries.push({
          name: name,
          kind: "builtin",
          status: row.status || "unknown",
          href: "#row-builtins-" + index,
          haystack: (row.label + " " + names.join(" ") + " " + (row.note || "")).toLowerCase()
        });
      });
    });

    (doc.stdlib || []).forEach(function (row, index) {
      var names = row.names && row.names.length ? row.names : [row.label];
      names.forEach(function (name) {
        entries.push({
          name: name,
          kind: "stdlib",
          status: row.status || "unknown",
          href: "#row-stdlib-" + index,
          haystack: (row.label + " " + names.join(" ") + " " + (row.unsupported || []).join(" ") +
            " " + (row.fences || []).join(" ") + " " + (row.note || "")).toLowerCase()
        });
      });
    });

    (doc.packages || []).forEach(function (row, index) {
      entries.push({
        name: row.name,
        kind: "package",
        status: row.status || "unknown",
        href: row.pypi || "#row-packages-" + index,
        external: !!row.pypi,
        haystack: (row.name + " " + (row.import_name || "") + " " + (row.version || "") + " " +
          (row.codes || []).map(function (c) { return c.code; }).join(" ")).toLowerCase()
      });
    });

    return entries;
  }

  function makeSearchItem(entry) {
    var li = el("li", "search-item");
    var a = el("a", "search-link");
    if (entry.external) {
      a.target = "_blank";
      a.rel = "noopener";
    }

    var nameWrap = el("span", "search-name-wrap");
    nameWrap.appendChild(el("span", "search-name", entry.name));
    nameWrap.appendChild(el("span", "search-kind", entry.kind));

    var statusWrap = el("span", "search-status");
    var dot = el("span", "dot status-" + entry.status);
    dot.setAttribute("aria-hidden", "true");
    statusWrap.appendChild(dot);
    statusWrap.appendChild(el("span", "search-status-text", STATUS_TEXT[entry.status] || entry.status));

    a.appendChild(nameWrap);
    a.appendChild(statusWrap);
    a.href = entry.href;
    li.appendChild(a);
    return li;
  }

  // exact > prefix > substring, the same ranking the reference site uses.
  function rank(term, entry) {
    var name = entry.name.toLowerCase();
    if (name === term) return -2;
    if (name.indexOf(term) === 0) return -1;
    return 0;
  }

  function wireSearch(index) {
    var input = document.getElementById("search-input");
    var list = document.getElementById("search-list");
    var heading = document.getElementById("search-heading");
    var matches = document.getElementById("search-matches");
    var shortcut = document.querySelector(".search-shortcut");
    if (!input || !list) return;

    function render(entries) {
      list.textContent = "";
      entries.forEach(function (entry) { list.appendChild(makeSearchItem(entry)); });
      if (heading) {
        heading.textContent = entries.length + (entries.length === 1 ? " module matches" : " modules match");
      }
    }

    function filter() {
      var term = input.value.trim().toLowerCase();
      if (!term) {
        matches.hidden = true;
        list.textContent = "";
        return;
      }
      matches.hidden = false;
      var found = index.filter(function (entry) {
        return entry.haystack.indexOf(term) !== -1;
      });
      found.sort(function (a, b) {
        return rank(term, a) - rank(term, b) || a.name.localeCompare(b.name);
      });
      render(found);
    }

    input.addEventListener("input", filter);

    input.addEventListener("focus", function () {
      if (shortcut) shortcut.classList.add("is-hidden");
    });

    input.addEventListener("blur", function () {
      if (!input.value && shortcut) shortcut.classList.remove("is-hidden");
    });

    document.addEventListener("keydown", function (event) {
      var active = document.activeElement;
      var typing = active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA" || active.isContentEditable);
      if (event.key === "/" && !typing) {
        event.preventDefault();
        input.focus();
      }
    });
  }

  // --- Heat grid. One square per probed item, grouped by category, in the
  // same GitHub-style layout the reference site uses.
  function renderHeat(doc) {
    var grid = document.getElementById("heat-grid");
    if (!grid) return;
    var groups = [];

    (doc.builtins || []).forEach(function (row, index) {
      groups.push({ label: "Builtins", status: row.status, name: row.label, href: "#row-builtins-" + index });
    });
    (doc.stdlib || []).forEach(function (row, index) {
      groups.push({ label: "Standard library", status: row.status, name: row.label, href: "#row-stdlib-" + index });
    });
    (doc.packages || []).forEach(function (row, index) {
      groups.push({ label: "PyPI packages", status: row.status, name: row.name, href: "#row-packages-" + index });
    });

    if (!groups.length) return;

    grid.textContent = "";
    groups.forEach(function (item) {
      var cell = el("span", "heat-cell status-" + (item.status || "unknown"));
      cell.setAttribute("role", "link");
      cell.setAttribute("tabindex", "0");
      cell.setAttribute("title", item.label + " \u00b7 " + item.name + " \u00b7 " + (STATUS_TEXT[item.status] || "unknown"));
      cell.setAttribute("aria-label", cell.getAttribute("title"));
      cell.dataset.href = item.href;
      grid.appendChild(cell);
    });

    function go(event) {
      var target = event.target.closest(".heat-cell");
      if (!target) return;
      var href = target.dataset.href;
      if (!href) return;
      var row = document.querySelector(href);
      if (row) {
        row.scrollIntoView({ block: "center" });
      }
    }
    grid.addEventListener("click", go);
    grid.addEventListener("keydown", function (event) {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        go(event);
      }
    });

    document.getElementById("heat").hidden = false;
  }

  function renderHistory(doc) {
    var history = doc.history || [];
    var metric = document.getElementById("history-metric");
    var chart = document.getElementById("chart");
    if (!history.length) {
      metric.hidden = true;
      chart.appendChild(el("p", "empty", "no history points yet"));
      return;
    }
    var labels = {
      weighted_pass: "weighted pass",
      packages_full: "packages full",
      packages_partial: "packages partial",
      packages_none: "packages none",
      fences: "fences"
    };
    function draw() {
      var key = metric.value;
      var values = history.map(function (p) { return Number(p[key] || 0); });
      var width = 960;
      var height = 260;
      var pad = { left: 48, right: 16, top: 14, bottom: 30 };
      var min = Math.min.apply(null, values);
      var max = Math.max.apply(null, values);
      if (min === max) { min = 0; max = Math.max(max, 1); }
      var span = max - min || 1;
      function x(i) { return history.length === 1 ? width / 2 : pad.left + (width - pad.left - pad.right) * i / (history.length - 1); }
      function y(v) { return height - pad.bottom - (height - pad.top - pad.bottom) * (v - min) / span; }
      var parts = [];
      parts.push('<svg viewBox="0 0 ' + width + " " + height + '" preserveAspectRatio="xMidYMid meet">');
      for (var g = 0; g <= 4; g++) {
        var gy = pad.top + (height - pad.top - pad.bottom) * g / 4;
        var gv = max - span * g / 4;
        parts.push('<line class="grid" x1="' + pad.left + '" y1="' + gy + '" x2="' + (width - pad.right) + '" y2="' + gy + '"/>');
        parts.push('<text x="' + (pad.left - 6) + '" y="' + (gy + 3) + '" text-anchor="end">' + fmt(gv) + "</text>");
      }
      parts.push('<line class="axis" x1="' + pad.left + '" y1="' + (height - pad.bottom) + '" x2="' + (width - pad.right) + '" y2="' + (height - pad.bottom) + '"/>');
      var points = values.map(function (v, i) { return x(i).toFixed(1) + "," + y(v).toFixed(1); }).join(" ");
      parts.push('<polyline class="series" points="' + points + '"/>');
      history.forEach(function (p, i) {
        parts.push('<circle class="point" cx="' + x(i).toFixed(1) + '" cy="' + y(values[i]).toFixed(1) + '" r="3"/>');
        parts.push('<text x="' + x(i).toFixed(1) + '" y="' + (height - 10) + '" text-anchor="middle">' + p.date + "</text>");
      });
      parts.push("</svg>");
      chart.innerHTML = parts.join("");
      chart.setAttribute("aria-label", "History chart, " + labels[key] + " over " + history.length + " nightly measurements, latest " + fmt(values[values.length - 1]));
    }
    function fmt(v) {
      if (key === "weighted_pass") return Math.round(v * 1000) / 10 + "%";
      return String(Math.round(v * 10) / 10);
    }
    metric.addEventListener("change", draw);
    draw();
  }

  function renderLadder(doc) {
    var ladder = doc.ladder;
    if (!ladder) return;
    var section = document.getElementById("ladder");
    var rungs = ladder.rungs || [];
    var lanes = ladder.lanes || [];
    if (!rungs.length && !lanes.length) return;
    section.hidden = false;

    var table = document.getElementById("ladder-table");
    table.textContent = "";
    var thead = el("thead");
    var hr = el("tr");
    ["Rung", "Corpus", "Compiles", "Matches CPython"].forEach(function (c) {
      hr.appendChild(el("th", null, c));
    });
    thead.appendChild(hr);
    table.appendChild(thead);
    var tbody = el("tbody");
    rungs.forEach(function (r) {
      var tr = el("tr");
      tr.appendChild(cell("td name", r.rung + " " + r.label));
      tr.appendChild(cell("td", r.corpus));
      tr.appendChild(cellNum(r.compiled + " of " + r.total));
      var outcome;
      if (r.measured) {
        var pct = r.total ? Math.round(1000 * r.passed / r.total) / 10 : 0;
        outcome = pct + "% (" + r.passed + " of " + r.total + ")";
      } else {
        outcome = "not measured";
      }
      tr.appendChild(cellNum(outcome));
      tbody.appendChild(tr);
    });
    if (!rungs.length) {
      var tr = el("tr");
      var td = el("td", "empty", "no ladder data yet");
      td.colSpan = 4;
      tr.appendChild(td);
      tbody.appendChild(tr);
    }
    table.appendChild(tbody);

    var laneTable = document.getElementById("lanes-table");
    laneTable.textContent = "";
    var lthead = el("thead");
    var lhr = el("tr");
    ["Lane", "Imports", "Share"].forEach(function (c) {
      lhr.appendChild(el("th", null, c));
    });
    lthead.appendChild(lhr);
    laneTable.appendChild(lthead);
    var ltbody = el("tbody");
    lanes.forEach(function (r) {
      var tr = el("tr");
      tr.appendChild(cell("td name", r.lane));
      tr.appendChild(cellNum(r.imports));
      tr.appendChild(cellNum(Math.round(1000 * r.share) / 10 + "%"));
      ltbody.appendChild(tr);
    });
    laneTable.appendChild(ltbody);
  }

  function renderMeta(doc) {
    var meta = document.getElementById("meta");
    var generated = document.getElementById("generated");
    var commit = document.getElementById("commit");
    var when = doc.generated || "an unknown date";
    meta.textContent = "measured " + when;
    generated.textContent = when;
    if (doc.commit) {
      commit.href = "https://github.com/sigilco/pyscriptc/commit/" + doc.commit;
      commit.textContent = doc.commit.slice(0, 12);
    } else {
      commit.textContent = "an unknown commit";
    }
  }

  function showError(message) {
    var box = document.getElementById("error");
    box.hidden = false;
    box.textContent = message;
  }

  function reveal(doc) {
    renderMeta(doc);
    renderSummary(doc);
    renderLadder(doc);
    renderHeroStat(doc);
    var anyRows = false;
    Object.keys(SECTIONS).forEach(function (key) {
      var rows = doc[key] || [];
      var section = document.getElementById(key);
      if (!rows.length) {
        section.appendChild(el("p", "empty", "no data in this section yet"));
        return;
      }
      anyRows = true;
      section.hidden = false;
      var st = state[key] = { rows: rows, filter: "", status: "all" };
      st.count = document.getElementById(key + "-count");
      renderTable(key);
      wireControls(key);
    });
    if (doc.history && doc.history.length) {
      document.getElementById("history").hidden = false;
      renderHistory(doc);
    }
    wireSearch(buildSearchIndex(doc));
    renderHeat(doc);
    if (!anyRows && !(doc.history || []).length) {
      showError("The data file has no rows. This is a data problem, not a page problem.");
    }
  }

  function load() {
    fetch(DATA_URL, { headers: { Accept: "application/json" } })
      .then(function (res) {
        if (!res.ok) throw new Error("HTTP " + res.status + " for " + DATA_URL);
        return res.json();
      })
      .then(reveal)
      .catch(function (err) {
        showError("Could not load the compat data (" + err.message + "). The JSON lives at " + DATA_URL + ".");
      });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", load);
  } else {
    load();
  }
})();
