// Find Your Job - Applications: every application in jobtrack, filterable, editable.
// Uses api(), toast() and $() from app.js and h() from setup.js.
"use strict";

const STATUS_LABEL = {
  wishlist: "Wishlist", applied: "Applied", screening: "Screening", interviewing: "Interviewing",
  offer: "Offer", rejected: "Rejected", withdrawn: "Withdrawn",
};
const FIELDS = [
  ["company", "Company"], ["role", "Role"], ["status", "Status"], ["applied_on", "Applied on"],
  ["url", "Link to the posting"], ["location", "Location"], ["salary", "Salary"], ["contact", "Contact"],
];

const af = { statuses: new Set(), open: true, quiet: false, q: "" };   // filters
let appsData = null;
let appSel = null;       // selected application id, or "new"
let appDetail = null;
let searchTimer = null;

function filterQuery() {
  const p = new URLSearchParams();
  for (const s of af.statuses) p.append("status", s);
  if (af.open) p.set("open", "1");
  if (af.quiet) p.set("quiet", "1");
  if (af.q.trim()) p.set("q", af.q.trim());
  return p.toString();
}

const daysSince = (iso) => Math.floor((Date.now() - new Date(`${iso}T00:00:00`).getTime()) / 86400000);

async function openApps(keepSelection = false) {
  try {
    appsData = await api(`/api/applications?${filterQuery()}`);
  } catch (err) {
    $("apps-view").replaceChildren(h("div", { class: "empty panel" }, h("h2", {}, "Applications could not load"), h("p", {}, err.message)));
    return;
  }
  if (!keepSelection || (appSel !== "new" && !appsData.apps.some((a) => a.id === appSel))) {
    appSel = appsData.apps.length ? appsData.apps[0].id : null;
  }
  appDetail = null;
  if (appSel !== null && appSel !== "new") {
    try { appDetail = await api(`/api/applications/${appSel}`); } catch { appDetail = null; }
  }
  drawApps();
}

function drawApps() {
  const s = appsData.stats;
  const summary = s.total
    ? [
        `${s.total} application${s.total === 1 ? "" : "s"}: ${s.open} open, ${s.closed} closed.`,
        s.quiet ? ` ${s.quiet} quiet for ${appsData.quiet_days}+ days.` : "",
        s.offer_rate !== null ? ` Offer rate ${s.offer_rate}% of ${s.decided} decided.` : "",
      ].join("")
    : "No applications yet. They appear here as you build CVs, or add one yourself.";

  const chips = appsData.statuses.map((st) => h("button", {
    type: "button", class: "btn", "aria-pressed": String(af.statuses.has(st)),
    onclick: () => { af.statuses.has(st) ? af.statuses.delete(st) : af.statuses.add(st); openApps(true); },
  }, `${STATUS_LABEL[st]} ${s.by_status[st]}`));

  const search = h("input", {
    type: "text", id: "apps-search", placeholder: "Search company, role, notes", value: af.q,
    oninput: (e) => { af.q = e.target.value; clearTimeout(searchTimer); searchTimer = setTimeout(() => openApps(true), 250); },
  });

  const bar = h("div", { class: "actions filters" },
    search,
    h("label", { class: "field check" }, h("input", { type: "checkbox", checked: af.open, onchange: (e) => { af.open = e.target.checked; openApps(true); } }), h("span", {}, "Open only")),
    h("label", { class: "field check" }, h("input", { type: "checkbox", checked: af.quiet, onchange: (e) => { af.quiet = e.target.checked; openApps(true); } }), h("span", {}, "Quiet only")),
    h("button", { type: "button", class: "btn primary", onclick: () => { appSel = "new"; appDetail = null; drawApps(); } }, "Add an application"),
    h("a", { class: "btn", href: `/api/applications.csv?${filterQuery()}`, download: "" }, "Export these to CSV"));

  const rows = appsData.apps.length
    ? appsData.apps.map((a) => h("button", {
        type: "button", class: "row app-row", role: "option", "aria-selected": String(a.id === appSel),
        onclick: async () => { appSel = a.id; appDetail = await api(`/api/applications/${a.id}`); drawApps(); },
      },
        h("span", { class: `pill ${a.status}` }, STATUS_LABEL[a.status] || a.status),
        h("span", { class: "who" }, h("b", {}, a.company), " ", a.role),
        h("span", { class: "when num" }, a.quiet ? h("span", { class: "quiet" }, `quiet ${daysSince(a.updated)} days`) : a.applied_on)))
    : [h("p", { class: "muted", style: "padding:14px" }, s.total ? "Nothing matches these filters." : "Nothing here yet.")];

  $("apps-view").replaceChildren(
    h("p", { class: "muted", style: "margin:8px 0 10px" }, summary),
    h("div", { class: "actions chips" }, chips),
    bar,
    h("div", { class: "split" },
      h("div", { class: "list", role: "listbox", "aria-label": "Applications" }, rows),
      h("article", { class: "detail" }, appSel === "new" ? drawNewApp() : appDetail ? drawAppDetail(appDetail) : h("p", { class: "muted" }, "Pick an application on the left."))));
}

function fieldControls(values) {
  const inputs = {};
  const controls = FIELDS.map(([key, label]) => {
    let control;
    if (key === "status") {
      control = h("select", {}, appsData.statuses.map((st) => h("option", { value: st, selected: st === values.status }, STATUS_LABEL[st])));
    } else if (key === "applied_on") {
      control = h("input", { type: "date", value: values.applied_on || "" });
    } else {
      control = h("input", { type: "text", value: values[key] || "" });
    }
    inputs[key] = control;
    return h("label", { class: "field" }, h("span", {}, label), control);
  });
  return { inputs, controls };
}

function drawAppDetail(a) {
  const { inputs, controls } = fieldControls(a);
  const note = h("textarea", { rows: 3, placeholder: "e.g. Phone screen booked for Tuesday" });
  const save = async () => {
    const fields = {};
    for (const [key] of FIELDS) if (String(inputs[key].value) !== String(a[key] ?? "")) fields[key] = inputs[key].value;
    if (!Object.keys(fields).length) { toast("Nothing changed."); return; }
    try {
      await api(`/api/applications/${a.id}`, { fields });
      toast("Saved");
      await openApps(true);
    } catch (err) { toast(err.message); }
  };
  const act = (path, body, done) => async () => {
    try { await api(`/api/applications/${a.id}${path}`, body); toast(done); await openApps(true); } catch (err) { toast(err.message); }
  };
  return h("div", { class: "form" },
    h("h2", {}, `${a.company}, ${a.role}`),
    h("p", { class: "where" }, `#${a.id}. Applied ${a.applied_on}; last change ${a.updated_at.slice(0, 10)}.`,
      a.url ? h("span", {}, " ", h("a", { href: a.url, target: "_blank", rel: "noopener noreferrer" }, "Open the posting")) : null),
    a.quiet ? h("div", { class: "note" },
      h("b", {}, `Quiet for ${daysSince(a.updated_at.slice(0, 10))} days. `), "Did they get back to you?",
      h("div", { class: "actions", style: "margin:8px 0 0" },
        h("button", { type: "button", class: "btn", onclick: act("/quick", { action: "no_reply" }, "Closed as no reply") }, "No reply, close it"),
        h("button", { type: "button", class: "btn", onclick: act("/quick", { action: "heard_back" }, "Moved to screening") }, "Heard back"))) : null,
    controls,
    h("div", { class: "actions" }, h("button", { type: "button", class: "btn primary", onclick: save }, "Save changes")),
    h("h3", {}, `Notes (${a.notes.length})`),
    a.notes.length ? h("ul", { class: "notes" }, a.notes.map((n) => h("li", {}, n))) : h("p", { class: "muted" }, "No notes yet."),
    h("label", { class: "field" }, h("span", {}, "Add a note"), note),
    h("div", { class: "actions" },
      h("button", { type: "button", class: "btn", onclick: async () => { if (!note.value.trim()) return; await act("/note", { text: note.value }, "Note added")(); } }, "Add note"),
      h("button", {
        type: "button", class: "btn danger",
        onclick: () => { if (window.confirm(`Delete ${a.company}, ${a.role}? This cannot be undone.`)) act("/delete", {}, "Deleted")(); },
      }, "Delete")));
}

function drawNewApp() {
  const today = new Date().toISOString().slice(0, 10);
  const { inputs, controls } = fieldControls({ status: "applied", applied_on: today });
  const note = h("textarea", { rows: 3 });
  const add = async () => {
    const fields = { note: note.value };
    for (const [key] of FIELDS) fields[key] = inputs[key].value;
    try {
      const app = await api("/api/applications", { fields });
      toast(`Added #${app.id}`);
      appSel = app.id;
      await openApps(true);
    } catch (err) { toast(err.message); }
  };
  return h("div", { class: "form" },
    h("h2", {}, "Add an application"),
    h("p", { class: "where" }, "For one you sent outside the daily loop. Company and role are required."),
    controls,
    h("label", { class: "field" }, h("span", {}, "First note (optional)"), note),
    h("div", { class: "actions" },
      h("button", { type: "button", class: "btn primary", onclick: add }, "Add application"),
      h("button", { type: "button", class: "btn", onclick: () => openApps() }, "Cancel")));
}
