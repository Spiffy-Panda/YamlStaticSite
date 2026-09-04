/* yss runtime: navigation toggle and dynamic JSON sections.
   A dynamic section is <div data-dynamic data-source="name" data-view="table|kv|list|cards|json|custom" ...>.
   It fetches <base>dynamic/<name>.json, which is {source, collected_at, ok, data | error}. */
(function () {
  "use strict";
  var root = document.documentElement;
  var base = root.getAttribute("data-base") || "/";

  var toggle = document.querySelector(".nav-toggle");
  if (toggle) {
    toggle.addEventListener("click", function () {
      var nav = document.getElementById("site-nav");
      var open = nav.classList.toggle("open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) { node.setAttribute(k, attrs[k]); });
    (children || []).forEach(function (c) { node.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
    return node;
  }
  function getPath(obj, path) {
    if (!path) return obj;
    return path.split(".").reduce(function (cur, part) { return cur == null ? undefined : cur[part]; }, obj);
  }
  function fmt(value) {
    if (value == null) return "";
    if (typeof value === "object") return JSON.stringify(value);
    return String(value);
  }
  function tone(value) {
    var v = String(value || "").toLowerCase().replace(/[^a-z0-9]+/g, "-");
    return v ? "tone-" + v : "";
  }

  var views = {
    json: function (container, data) {
      container.appendChild(el("pre", {}, [JSON.stringify(data, null, 2)]));
    },
    kv: function (container, data) {
      var dl = el("dl", { "class": "kv" });
      Object.keys(data || {}).forEach(function (k) {
        dl.appendChild(el("dt", {}, [k]));
        dl.appendChild(el("dd", {}, [fmt(data[k])]));
      });
      container.appendChild(dl);
    },
    list: function (container, data) {
      var ul = el("ul");
      (Array.isArray(data) ? data : []).forEach(function (item) { ul.appendChild(el("li", {}, [fmt(item)])); });
      container.appendChild(ul);
    },
    table: function (container, data, cfg) {
      var rows = Array.isArray(data) ? data : [];
      var columns = cfg.columns.length ? cfg.columns : Object.keys(rows[0] || {});
      columns = columns.map(function (c) { return typeof c === "string" ? { key: c, label: c } : c; });
      var table = el("table", { "class": "table dyn-table" });
      var thead = el("thead"); var tr = el("tr");
      columns.forEach(function (c) { tr.appendChild(el("th", {}, [c.label || c.key])); });
      thead.appendChild(tr); table.appendChild(thead);
      var tbody = el("tbody");
      rows.forEach(function (row) {
        var r = el("tr");
        columns.forEach(function (c) {
          var v = getPath(row, c.key);
          var td = el("td", {}, [fmt(v)]);
          if (typeof v === "string" && v.length < 20) td.className = tone(v);
          r.appendChild(td);
        });
        tbody.appendChild(r);
      });
      table.appendChild(tbody);
      if (!rows.length) container.appendChild(el("p", { "class": "dynamic-status" }, ["(empty list)"]));
      container.appendChild(table);
    },
    cards: function (container, data, cfg) {
      var f = cfg.fields || {};
      var grid = el("div", { "class": "card-grid cols-3 dyn-cards" });
      (Array.isArray(data) ? data : []).forEach(function (item) {
        var card = el("div", { "class": "card" });
        var badge = getPath(item, f.badge || "status");
        var head = el("div", { "class": "card-head" }, [el("h3", { "class": "card-title" }, [fmt(getPath(item, f.title || "title"))])]);
        if (badge != null) head.appendChild(el("span", { "class": "badge " + tone(badge) }, [fmt(badge)]));
        card.appendChild(head);
        var sub = getPath(item, f.subtitle || "subtitle");
        if (sub != null) card.appendChild(el("div", { "class": "card-subtitle" }, [fmt(sub)]));
        var body = getPath(item, f.body || "body");
        if (body != null) card.appendChild(el("div", { "class": "card-body" }, [fmt(body)]));
        grid.appendChild(card);
      });
      container.appendChild(grid);
    },
    custom: function (container, data, cfg, envelope) {
      var script = document.querySelector('script[type="text/x-yss-render"][data-for="' + cfg.section + '"]');
      if (!script) { container.appendChild(el("p", { "class": "dynamic-error" }, ["custom view has no script"])); return; }
      try {
        new Function("el", "data", "envelope", "h", script.textContent)(container, data, envelope, el);
      } catch (err) {
        container.appendChild(el("p", { "class": "dynamic-error" }, ["render script failed: " + err.message]));
      }
    }
  };

  function render(node, envelope) {
    var cfg = {
      section: node.getAttribute("data-section"),
      view: node.getAttribute("data-view") || "json",
      path: node.getAttribute("data-path") || "",
      columns: JSON.parse(node.getAttribute("data-columns") || "[]"),
      fields: JSON.parse(node.getAttribute("data-fields") || "{}")
    };
    node.innerHTML = "";
    if (!envelope.ok) {
      node.appendChild(el("p", { "class": "dynamic-error" }, ["source failed: " + (envelope.error || "unknown error")]));
    } else {
      var data = getPath(envelope.data, cfg.path);
      (views[cfg.view] || views.json)(node, data, cfg, envelope);
    }
    var meta = el("div", { "class": "dynamic-meta" }, ["source " + envelope.source + " · collected " + (envelope.collected_at || "?")]);
    var btn = el("button", { type: "button" }, ["refresh"]);
    btn.addEventListener("click", function () { load(node, true); });
    meta.appendChild(btn);
    node.appendChild(meta);
  }

  function load(node, force) {
    var source = node.getAttribute("data-source");
    if (node.getAttribute("data-available") === "0") {
      node.innerHTML = "";
      node.appendChild(el("p", { "class": "dynamic-status" }, [node.getAttribute("data-empty") || "Not available in this build."]));
      return;
    }
    var url = base + "dynamic/" + source + ".json?t=" + Date.now() + (force ? "&refresh=1" : "");
    fetch(url).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    }).then(function (envelope) {
      render(node, envelope);
      var refresh = parseInt(node.getAttribute("data-refresh") || "0", 10);
      if (refresh > 0) setTimeout(function () { load(node, false); }, refresh * 1000);
    }).catch(function (err) {
      node.innerHTML = "";
      node.appendChild(el("p", { "class": "dynamic-status" }, [node.getAttribute("data-empty") || "No data.", " (" + err.message + ")"]));
    });
  }

  // "Show sources" (gh-29): reveal the per-section provenance captions the build emitted.
  // A button and a class, not a hover pill - hover has no touch equivalent, <section> is not
  // focusable, and a pointer-following live region is an anti-pattern for assistive tech.
  (function sources() {
    var toggle = document.querySelector(".source-toggle");
    if (!toggle) return;
    var KEY = "yss.showSources";
    function apply(on) {
      document.querySelectorAll(".section-source").forEach(function (node) { node.hidden = !on; });
      toggle.setAttribute("aria-pressed", on ? "true" : "false");
      toggle.textContent = on ? "Hide sources" : "Show sources";
    }
    var stored = null;
    try { stored = localStorage.getItem(KEY); } catch (e) { stored = null; }
    apply(stored === null ? toggle.getAttribute("data-default") === "1" : stored === "1");
    toggle.addEventListener("click", function () {
      var on = toggle.getAttribute("aria-pressed") !== "true";
      apply(on);
      try { localStorage.setItem(KEY, on ? "1" : "0"); } catch (e) { /* private mode: this session only */ }
    });
  })();

  document.querySelectorAll("[data-dynamic]").forEach(function (node) { load(node, false); });
  window.yss = { base: base, loadDynamic: load, el: el, getPath: getPath };
})();
