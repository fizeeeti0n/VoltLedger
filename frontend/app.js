/* VoltLedger frontend. All dynamic text goes through textContent (ledger fields are untrusted). */
(function () {
  "use strict";
  const $ = (s) => document.querySelector(s);
  const COLORS = { Winter: "#3b82f6", Summer: "#f59e0b", Rainy: "#0f766e" };
  const fmt = (n, d = 0) => n == null ? "-" : Number(n).toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });
  const tk = (n, d = 0) => n == null ? "-" : "Tk " + fmt(n, d);
  const dstr = (iso) => new Date(iso + "T00:00:00").toLocaleDateString("en-GB", { day: "2-digit", month: "short", year: "2-digit" });

  let token = localStorage.getItem("vl_token");
  let email = localStorage.getItem("vl_email");
  let current = null;       // last analysis result
  let charts = [];
  let authMode = "login";

  function el(tag, props, ...kids) {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(props || {})) {
      if (k === "class") e.className = v;
      else if (k === "style") e.style.cssText = v;
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
      else if (v !== false && v != null) e.setAttribute(k, v);
    }
    for (const kid of kids.flat()) if (kid != null) e.append(kid.nodeType ? kid : document.createTextNode(String(kid)));
    return e;
  }

  async function api(path, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    if (token) headers.Authorization = "Bearer " + token;
    const res = await fetch(path, Object.assign({}, opts, { headers }));
    if (!res.ok) {
      let msg = "Something went wrong.";
      try { const j = await res.json(); msg = typeof j.detail === "string" ? j.detail : (j.detail && j.detail[0] && j.detail[0].msg) || msg; } catch (e) {}
      if (res.status === 401 && token) signOut(true);
      throw new Error(msg);
    }
    return res;
  }

  /* ---------------- nav + auth ---------------- */
  function renderNav() {
    const nav = $("#nav");
    nav.replaceChildren();
    if (token) {
      nav.append(el("button", { class: "btn ghost", onclick: showSaved }, "My reports"),
        el("span", { class: "muted" }, email || ""),
        el("button", { class: "btn", onclick: () => signOut(false) }, "Sign out"));
    } else {
      nav.append(el("button", { class: "btn", onclick: () => openAuth("login") }, "Sign in"),
        el("button", { class: "btn primary", onclick: () => openAuth("register") }, "Create account"));
    }
  }
  function openAuth(mode) {
    authMode = mode;
    $("#authTitle").textContent = mode === "login" ? "Sign in" : "Create account";
    $("#authSubmit").textContent = mode === "login" ? "Sign in" : "Create account";
    $("#authSwitch").textContent = mode === "login" ? "Create an account" : "I already have an account";
    $("#password").autocomplete = mode === "login" ? "current-password" : "new-password";
    $("#authErr").hidden = true;
    $("#authDlg").showModal();
  }
  function setSession(t, e) {
    token = t; email = e;
    localStorage.setItem("vl_token", t); localStorage.setItem("vl_email", e);
    renderNav(); if (current) renderResults(current);
  }
  function signOut(expired) {
    token = email = null;
    localStorage.removeItem("vl_token"); localStorage.removeItem("vl_email");
    $("#saved").hidden = true;
    renderNav(); if (current) renderResults(current);
    if (expired) showError("Your session expired. Please sign in again.");
  }
  $("#authSwitch").addEventListener("click", (e) => { e.preventDefault(); openAuth(authMode === "login" ? "register" : "login"); });
  $("#authCancel").addEventListener("click", () => $("#authDlg").close());
  $("#authForm").addEventListener("submit", async (e) => {
    e.preventDefault();
    const btn = $("#authSubmit"); btn.disabled = true;
    try {
      const res = await api("/api/auth/" + authMode, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ email: $("#email").value, password: $("#password").value })
      });
      const j = await res.json();
      $("#authDlg").close(); $("#password").value = "";
      setSession(j.token, j.email);
    } catch (err) {
      $("#authErr").textContent = err.message; $("#authErr").hidden = false;
    } finally { btn.disabled = false; }
  });

  /* ---------------- upload ---------------- */
  const drop = $("#drop"), fileInput = $("#file");
  drop.addEventListener("click", () => fileInput.click());
  drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); fileInput.click(); } });
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => { if (e.dataTransfer.files[0]) upload(e.dataTransfer.files[0]); });
  fileInput.addEventListener("change", () => { if (fileInput.files[0]) upload(fileInput.files[0]); fileInput.value = ""; });

  function showError(msg) { const e = $("#error"); e.textContent = msg; e.hidden = !msg; }

  async function upload(file) {
    showError("");
    if (file.size > 10 * 1024 * 1024) return showError("File too large (limit 10 MB).");
    $("#loading").hidden = false;
    const wait = setTimeout(() => { $("#loadingText").textContent = "Waking the server (free hosting sleeps when idle, this can take up to a minute)..."; }, 5000);
    const fd = new FormData(); fd.append("file", file);
    try {
      const res = await api("/api/analyze", { method: "POST", body: fd });
      current = await res.json();
      renderResults(current);
      $("#results").scrollIntoView({ behavior: "smooth", block: "start" });
    } catch (err) { showError(err.message); }
    finally { clearTimeout(wait); $("#loadingText").textContent = "Reading your ledger..."; $("#loading").hidden = true; }
  }

  /* ---------------- results ---------------- */
  function kpi(l, v) { return el("div", { class: "card kpi" }, el("div", { class: "l" }, l), el("div", { class: "v" }, v)); }

  function renderResults(a) {
    charts.forEach((c) => c.destroy()); charts = [];
    const root = $("#results"); root.replaceChildren(); root.hidden = false;
    const k = a.kpis, m = a.meta;
    const who = [m.customer_name, m.customer_no && "Customer " + m.customer_no, m.meter_no && "Meter " + m.meter_no, m.tariff && "Tariff " + m.tariff].filter(Boolean).join("  |  ");

    const actions = el("div", { class: "actions" },
      el("button", { class: "btn primary", onclick: downloadXlsx }, "Download Excel report"));
    if (token) actions.append(el("button", { class: "btn", id: "saveBtn", onclick: saveReport }, a.id ? "Saved" : "Save to my reports"));
    else actions.append(el("button", { class: "btn", onclick: () => openAuth("register") }, "Sign in to save"));

    root.append(el("div", { class: "head" },
      el("div", {}, el("h2", {}, (m.utility || "Utility") + " report: " + dstr(k.first) + " to " + dstr(k.data_end)),
        el("div", { class: "sub" }, who || a.filename, a.elapsed_ms ? "  |  analysed in " + (a.elapsed_ms / 1000).toFixed(1) + " s" : "")),
      actions));

    if (m.confidence !== "high") root.append(el("div", { class: "alert warn" },
      "This file used a best-effort parser (" + m.confidence + " confidence). Please check the totals against your original. ",
      (m.warnings || []).join(" ")));

    root.append(el("div", { class: "grid kpis" },
      kpi("Total recharged", tk(k.total)), kpi("Recharges", fmt(k.n_recharges)), kpi("Avg per recharge", tk(k.avg_recharge)),
      kpi("Energy cost", tk(k.energy)), kpi("Energy share", fmt(k.energy_share, 0) + "%"),
      kpi("Avg burn rate", k.burn_per_day ? tk(k.burn_per_day) + "/day" : "-")));

    root.append(el("h3", { class: "t", style: "margin-top:22px" }, "By Bangladesh season (complete months, like-for-like)"));
    const sg = el("div", { class: "grid seasons" });
    a.seasons.forEach((s) => {
      const c = el("div", { class: "card season", style: "--c:" + COLORS[s.season] }, el("h3", {}, s.season), el("div", { class: "m" }, s.months_label));
      if (s.per_day) {
        c.append(el("div", { class: "big" }, tk(s.per_30), el("small", {}, " / 30 days")),
          el("div", { class: "m" }, tk(s.per_day, 1) + " per day  |  " + s.days + " days  |  " + tk(s.total) + " total"),
          el("span", { class: "idx" }, s.index === 1 ? "Lightest season" : s.index + "x the lightest"));
      } else c.append(el("div", { class: "na" }, "No complete months in this ledger yet."));
      sg.append(c);
    });
    root.append(sg);

    root.append(el("div", { class: "grid two", style: "margin-top:14px" },
      el("div", { class: "card" }, el("h3", { class: "t" }, "Monthly recharge (Tk)"), el("div", { class: "chartbox" }, el("canvas", { id: "cMonthly" }))),
      el("div", { class: "card" }, el("h3", { class: "t" }, "Burn rate per recharge (Tk/day)"), el("div", { class: "chartbox" }, el("canvas", { id: "cBurn" })))));

    root.append(el("div", { class: "card", style: "margin-top:14px" }, el("h3", { class: "t" }, "Key findings"),
      el("ul", { class: "find" }, a.findings.map((f) => el("li", {}, f)))));

    const tbl = el("table", {}, el("thead", {}, el("tr", {}, ["Month", "Season", "Recharges", "Total (Tk)", "Energy (Tk)", "Tk / day", "Tk / 30 days"].map((h) => el("th", {}, h)))),
      el("tbody", {}, a.monthly.map((x) => el("tr", {},
        el("td", {}, x.label + (x.full ? "" : " (partial)")),
        el("td", {}, el("span", { class: "pill", style: "background:" + COLORS[x.season] }, x.season)),
        el("td", {}, x.count), el("td", {}, fmt(x.recharge)), el("td", {}, fmt(x.energy)),
        el("td", {}, fmt(x.per_day, 1)), el("td", { class: x.full ? "" : "partial" }, fmt(x.per_30))))));
    root.append(el("div", { class: "card scroll", style: "margin-top:14px" }, el("h3", { class: "t" }, "Monthly breakdown"), tbl));

    if (a.warnings && a.warnings.length) root.append(el("p", { class: "muted", style: "margin-top:12px" }, a.warnings.join(" ")));
    drawCharts(a);
  }

  function drawCharts(a) {
    if (!window.Chart) return;
    const ink = getComputedStyle(document.body).color;
    Chart.defaults.color = ink;
    Chart.defaults.borderColor = "rgba(128,128,160,.25)";
    charts.push(new Chart($("#cMonthly"), {
      type: "bar",
      data: { labels: a.monthly.map((x) => x.label), datasets: [{ label: "Recharge (Tk)", data: a.monthly.map((x) => x.recharge), backgroundColor: a.monthly.map((x) => COLORS[x.season] + (x.full ? "" : "88")) }] },
      options: { maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } }
    }));
    const cy = a.cycles.filter((c) => c.closed);
    charts.push(new Chart($("#cBurn"), {
      type: "line",
      data: { labels: cy.map((c) => c.label), datasets: [{ label: "Tk/day", data: cy.map((c) => c.per_day), borderColor: "#1f3864", backgroundColor: cy.map((c) => COLORS[c.season]), pointRadius: 5, tension: .25 }] },
      options: { maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } }
    }));
  }

  function payload(a) {
    return { meta: a.meta, records: a.records, season_map: a.season_map, filename: a.filename || "ledger" };
  }

  async function downloadXlsx() {
    try {
      const res = a_isSaved() ? await api("/api/reports/" + current.id + "/xlsx")
        : await api("/api/export", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload(current)) });
      const blob = await res.blob();
      const cd = res.headers.get("Content-Disposition") || "";
      const name = (cd.match(/filename="([^"]+)"/) || [])[1] || "report.xlsx";
      const url = URL.createObjectURL(blob);
      const a = el("a", { href: url, download: name }); document.body.append(a); a.click(); a.remove();
      setTimeout(() => URL.revokeObjectURL(url), 5000);
    } catch (err) { showError(err.message); }
  }
  const a_isSaved = () => !!(current && current.id && token);

  async function saveReport() {
    try {
      const res = await api("/api/reports", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload(current)) });
      const j = await res.json();
      current.id = j.id;
      const b = $("#saveBtn"); if (b) { b.textContent = "Saved"; b.disabled = true; }
    } catch (err) { showError(err.message); }
  }

  /* ---------------- saved reports ---------------- */
  async function showSaved() {
    const box = $("#saved"); box.replaceChildren(); box.hidden = false;
    try {
      const list = await (await api("/api/reports")).json();
      box.append(el("div", { class: "head" }, el("h2", {}, "My reports")));
      const card = el("div", { class: "card" });
      if (!list.length) card.append(el("p", { class: "muted" }, "Nothing saved yet. Upload a ledger and choose Save to my reports."));
      list.forEach((r) => card.append(el("div", { class: "saved-row" },
        el("div", {}, el("b", {}, r.title), el("span", { class: "muted" }, r.filename + "  |  " + r.created)),
        el("div", { class: "actions" },
          el("button", { class: "btn", onclick: () => openSaved(r.id) }, "Open"),
          el("button", { class: "btn danger", onclick: async () => { if (confirm("Delete this report?")) { await api("/api/reports/" + r.id, { method: "DELETE" }); showSaved(); } } }, "Delete")))));
      box.append(card);
      box.scrollIntoView({ behavior: "smooth" });
    } catch (err) { showError(err.message); }
  }
  async function openSaved(id) {
    try { current = await (await api("/api/reports/" + id)).json(); renderResults(current); $("#results").scrollIntoView({ behavior: "smooth" }); }
    catch (err) { showError(err.message); }
  }

  renderNav();
  fetch("/api/health").catch(() => {});   // wakes a sleeping free-tier backend while the visitor picks a file
  if (token) api("/api/auth/me").catch(() => {});
})();
