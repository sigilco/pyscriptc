/* pyscriptc compat site. Plain DOM, no framework, no third-party script.
   Everything renders from data/compat.json (the schema lives in
   scripts/compat_schema.py next to the page source in the repository).
   The base path is baked in at build time as window.__COMPAT_BASE__.

   The page leads with what does not work: the divergences, the failures and
   the fences come before the support tables. Every module, package and fence
   code has a deep link, so a row can be shared. */
(function () {
  "use strict";

  var BASE = (window.__COMPAT_BASE !== undefined ? window.__COMPAT_BASE : "/compat/")
    .replace(/\/*$/, "/");
  var DATA_URL = BASE + "data/compat.json";

  var STATUS_EMOJI = { full: "\u{1F7E2}", partial: "\u{1F7E1}", none: "\u{1F534}", unknown: "\u2014" };
  var STATUS_TEXT = { full: "full", partial: "partial", none: "none", unknown: "unknown" };

  // A snapshot older than this many days is stale: the nightly runs daily, so
  // a snapshot that did not move in three days is not measuring anything.
  var STALE_DAYS = 3;

  // Per-section table config: thead, row builder, searchable text.
  var SECTIONS = {
    language: {
      section: "language",
      table: "language-table",
      columns: ["Group", "Probes", "Pass", "Fence", "Failures", "Weighted pass", "Fence codes"],
      row: function (r) {
        var outcomes = r.outcomes || {};
        var other = (outcomes["wrong-output"] || 0) + (outcomes.crash || 0) +
          (outcomes.timeout || 0) + (outcomes["build-error"] || 0);
        return [
          cell("td name", r.label),
          cellNum(outcomes.pass || 0),
          cellNum(outcomes.fence || 0),
          cellNum(other, other ? "bad" : null),
          cellNum(r.weighted_pass),
          cellCodes(r.fence_codes)
        ];
      },
      search: function (r) { return r.label + " " + r.id; }
    },
    builtins: {
      section: "builtins",
      table: "builtins-table",
      columns: ["Status", "Builtin", "Pass rate", "Probes", "Not supported", "Fences", "Note"],
      row: statusRow,
      search: function (r) {
        return r.label + " " + r.names.join(" ") + " " + r.unsupported.join(" ") +
          " " + r.fences.join(" ") + " " + (r.note || "");
      }
    },
    stdlib: {
      section: "stdlib",
      table: "stdlib-table",
      columns: ["Status", "Module", "Pass rate", "Probes", "Not supported", "Fences", "Note"],
      row: statusRow,
      search: function (r) {
        return r.label + " " + r.names.join(" ") + " " + r.unsupported.join(" ") +
          " " + r.fences.join(" ") + " " + (r.note || "");
      }
    },
    packages: {
      section: "pypi",
      table: "packages-table",
      columns: ["Status", "Package", "Pass rate", "Version", "Fences", "Definitions", "Top fence codes"],
      row: function (r) {
        return [
          statusCell(r.status),
          linkCell(r.name, r.pypi),
          rateCell(r.pass_rate, r.probes),
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
      section: "fences",
      table: "fences-table",
      columns: ["Code", "Title", "Count", "Packages", "Fix"],
      row: function (r) {
        return [
          cell("td name", r.code),
          cell("td", r.title),
          cellNum(r.count),
          cellNum(r.packages),
          fixCell(r.fix)
        ];
      },
      search: function (r) { return r.code + " " + r.title + " " + (r.fix || ""); }
    },
    targets: {
      section: "targets",
      table: "targets-table",
      columns: ["Status", "Target", "Triple", "Note", "Proven by"],
      row: function (r) {
        return [
          targetCell(r.status),
          cell("td name", r.label),
          cell("td name", r.triple),
          cell("td", r.note),
          cell("td dim", r.proof)
        ];
      },
      search: function (r) { return r.label + " " + r.triple + " " + r.family + " " + r.note; }
    },
    divergences: {
      section: "divergences",
      table: "divergences-table",
      columns: ["#", "Family", "Difference from CPython 3.14", "Source"],
      row: function (r) {
        return [
          cell("td divnum", r.number),
          cell("td", r.family),
          divTextCell(r.text),
          sourceCell(r.sources, r.note)
        ];
      },
      search: function (r) { return r.number + " " + r.family + " " + r.text; },
      // The family chips are built from the rows, so a new family needs no
      // markup change.
      chipsFrom: "family"
    }
  };

  // The list sections share one generic table engine. State lives per
  // section: the rows, the current filter text and the status chip.
  var state = {};

  function el(tag, cls, text) {
    var node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function cell(cls, text) { return el("td", cls, text); }

  function cellNum(value, extra) {
    var td = el("td", "num");
    td.appendChild(document.createTextNode(String(value)));
    if (extra === "bad") td.style.color = "var(--bad)";
    return td;
  }

  function cellCodes(codes) {
    var td = el("td");
    var list = codes || [];
    if (!list.length) {
      td.appendChild(el("span", "dim", "none"));
      return td;
    }
    list.forEach(function (c, i) {
      if (i) td.appendChild(document.createTextNode(", "));
      var text = c.count === null || c.count === undefined ? c.code : c.code + " \u00D7" + c.count;
      td.appendChild(codeLink(text, c.code));
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
      rateCell(r.pass_rate, r.probes),
      cellNum(r.probes === null || r.probes === undefined ? "-" : r.probes),
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

  function targetCell(status) {
    var td = el("td", "status status-" + (status === "native" ? "full" : "partial"));
    var emoji = el("span", "emoji", status === "native" ? "\u2714" : "\u26A0");
    emoji.setAttribute("role", "img");
    emoji.setAttribute("aria-label", status);
    td.appendChild(emoji);
    td.appendChild(el("span", "text", status));
    return td;
  }

  function rateCell(rate, probes) {
    var td = el("td");
    if (rate === null || rate === undefined) {
      td.appendChild(el("span", "rate-none", "not measured"));
      return td;
    }
    var wrap = el("span", "rate");
    var pct = Math.round(rate * 1000) / 10;
    var bar = el("span", "bar");
    var fill = el("span", "fill");
    if (pct < 25) fill.className = "fill fill-bad";
    else if (pct < 75) fill.className = "fill fill-warn";
    fill.style.width = Math.max(2, Math.min(100, pct)) + "%";
    bar.appendChild(fill);
    wrap.appendChild(bar);
    wrap.appendChild(document.createTextNode(pct + "%"));
    if (probes) wrap.appendChild(el("span", "dim", " of " + probes));
    td.appendChild(wrap);
    return td;
  }

  function fixCell(fix) {
    var td = el("td");
    td.appendChild(el("span", "fixhint", fix || "no hint recorded for this code"));
    return td;
  }

  // The divergence text comes from the markdown doc, so it carries inline
  // code spans and links. Rendering them as text would show the markdown
  // syntax, so the two inline forms are turned into elements.
  function inlineMarkdown(text) {
    var fragment = document.createDocumentFragment();
    var pattern = /`([^`]+)`|\[([^\]]+)\]\(([^)]+)\)/g;
    var last = 0;
    var match;
    while ((match = pattern.exec(text)) !== null) {
      if (match.index > last) {
        fragment.appendChild(document.createTextNode(text.slice(last, match.index)));
      }
      if (match[1] !== undefined) {
        fragment.appendChild(el("code", null, match[1]));
      } else {
        var a = el("a", null, match[2]);
        a.href = match[3];
        a.rel = "noopener";
        fragment.appendChild(a);
      }
      last = pattern.lastIndex;
    }
    if (last < text.length) fragment.appendChild(document.createTextNode(text.slice(last)));
    return fragment;
  }

  function divTextCell(text) {
    var td = el("td", "divtext");
    td.appendChild(inlineMarkdown(text));
    return td;
  }

  function sourceCell(sources, note) {
    var td = el("td");
    if (!sources || !sources.length) {
      var only = el("span", "dim");
      only.appendChild(inlineMarkdown(note || "in the tree"));
      td.appendChild(only);
      return td;
    }
    sources.forEach(function (href, i) {
      if (i) td.appendChild(document.createTextNode(", "));
      var a = el("a", "fixsrc", i === 0 ? "source" : String(i + 1));
      a.href = href;
      a.rel = "noopener";
      td.appendChild(a);
    });
    if (note && sources.length) {
      var span = el("span", "fixsrc");
      span.appendChild(document.createTextNode(" "));
      span.appendChild(inlineMarkdown(note));
      td.appendChild(span);
    }
    return td;
  }

  // A fence code is a deep link: /compat/#fence-PS2002 opens the census with
  // the code in view.
  function codeLink(text, code) {
    var a = el("a", "name", text);
    a.href = "#fence-" + code;
    a.setAttribute("data-fence", code);
    return a;
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
      if (st.status !== "all" && !matchesStatus(cfg, r, st.status)) return;
      if (needle && cfg.search(r).toLowerCase().indexOf(needle) === -1) return;
      var tr = el("tr");
      tr.id = rowId(key, r);
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

  // The status of a row depends on the section: the support rows have a
  // status vocabulary, the divergences have a family.
  function matchesStatus(cfg, row, wanted) {
    if (cfg.chipsFrom) return String(row[cfg.chipsFrom]) === wanted;
    return row.status === wanted;
  }

  function rowId(key, row) {
    if (key === "fences") return "fence-" + row.code;
    if (key === "divergences") return "divergence-" + row.number;
    if (key === "packages") return "package-" + slug(row.name);
    if (key === "targets") return "target-" + slug(row.id);
    if (key === "stdlib") return "stdlib-" + slug(row.names[0]);
    return key + "-" + slug(row.id || row.label);
  }

  function slug(value) {
    return String(value).toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
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

  // The family chips of the divergences table, built from the rows.
  function buildChips(key, rows) {
    var cfg = SECTIONS[key];
    if (!cfg.chipsFrom) return;
    var chips = document.querySelector('.chips[data-for="' + key + '"]');
    if (!chips) return;
    var families = [];
    rows.forEach(function (row) {
      if (families.indexOf(row[cfg.chipsFrom]) === -1) families.push(row[cfg.chipsFrom]);
    });
    families.sort();
    families.forEach(function (family) {
      var button = el("button", "chip", family);
      button.type = "button";
      button.dataset.status = family;
      button.setAttribute("aria-pressed", "false");
      chips.appendChild(button);
    });
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
    return parts.length ? parts.join(" \u00B7 ") : "no rows";
  }

  function renderSummary(doc) {
    var s = doc.summary || {};
    function fill(id, text) {
      var card = document.getElementById(id);
      if (!card) return;
      var target = card.querySelector(".counts") || card.querySelector(".big");
      if (target) target.textContent = text;
    }
    var rate = s.pass_rate;
    if (typeof rate === "number") {
      fill("card-probes", (Math.round(rate * 1000) / 10) + "%");
      var sub = document.getElementById("card-probes-sub");
      if (sub) sub.textContent = s.probe_pass + " of " + s.probes + " probes";
    } else {
      var probeCard = document.getElementById("card-probes");
      if (probeCard) probeCard.hidden = true;
    }
    fill("card-failures", typeof s.probe_failures === "number" ? String(s.probe_failures) : "-");
    fill("card-fences", typeof s.fences === "number" ? String(s.fences) : "-");
    fill("card-packages", countsLine(countBy(doc.packages || [])));
    fill("card-stdlib", countsLine(countBy(doc.stdlib || [])));
    fill("card-divergences", typeof s.divergences === "number" ? String(s.divergences) : "-");
    var targets = doc.targets || [];
    var native = targets.filter(function (t) { return t.status === "native"; }).length;
    var experimental = targets.filter(function (t) { return t.status === "experimental"; }).length;
    fill("card-targets", native + " native \u00B7 " + experimental + " experimental");
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
        parts.push('<text x="' + (pad.left - 6) + '" y="' + (gy + 3) + '" text-anchor="end">' + fmt(gv, key) + "</text>");
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
      chart.setAttribute("aria-label", "History chart, " + labels[key] + " over " + history.length + " nightly measurements, latest " + fmt(values[values.length - 1], key));
    }
    function fmt(v, key) {
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

  // The stale banner: a snapshot that did not move for days is not a
  // measurement, it is a leftover. The nightly runs daily.
  function renderStale(doc) {
    var banner = document.getElementById("stale");
    var detail = document.getElementById("stale-detail");
    if (!banner || !detail) return;
    var generated = doc.generated;
    if (!generated) return;
    var measured = Date.parse(generated + "T00:00:00Z");
    if (isNaN(measured)) return;
    var days = Math.floor((Date.now() - measured) / 86400000);
    if (days <= STALE_DAYS) return;
    detail.textContent = "The snapshot is from " + generated + ", " + days + " days ago.";
    banner.hidden = false;
  }

  function showError(message) {
    var box = document.getElementById("error");
    box.hidden = false;
    box.textContent = message;
  }

  function reveal(doc) {
    renderMeta(doc);
    renderStale(doc);
    renderSummary(doc);
    document.getElementById("summary").hidden = false;
    var anyRows = false;
    Object.keys(SECTIONS).forEach(function (key) {
      var rows = doc[key] || [];
      var section = document.getElementById(SECTIONS[key].section);
      if (!rows.length || !section) {
        return;
      }
      anyRows = true;
      section.hidden = false;
      buildChips(key, rows);
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
    applyDeepLink();
  }

  // A deep link is a hash: #fence-PS2002, #package-requests,
  // #stdlib-math, #divergence-4, #target-linux-x86_64. The link scrolls to
  // the row and highlights it.
  function applyDeepLink() {
    var hash = window.location.hash.replace(/^#/, "");
    if (!hash) return;
    var row = document.getElementById(hash);
    if (!row) return;
    row.scrollIntoView({ block: "center" });
    row.classList.add("row-focus");
    window.setTimeout(function () { row.classList.remove("row-focus"); }, 4000);
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
