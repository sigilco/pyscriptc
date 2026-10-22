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
    st.rows.forEach(function (r) {
      if (st.status !== "all" && r.status !== st.status) return;
      if (needle && cfg.search(r).toLowerCase().indexOf(needle) === -1) return;
      var tr = el("tr");
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
