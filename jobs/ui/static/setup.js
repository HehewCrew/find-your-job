// jobHunt - Setup. Draws a form from each personal file itself, guided by the small
// schema the server sends, and saves the whole object back so keys the form never shows
// survive. Uses api(), esc(), toast(), runTask() and showTab() from app.js.
"use strict";

let ed = null;          // the file being edited: {name, label, data | preamble+sections, schema, context, from_example}
let edError = "";       // last save error, shown above the form
let edWarnings = [];    // last save's build warnings
let previewResult = null;

// --- small DOM helper -------------------------------------------------------------

function h(tag, attrs = {}, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v === undefined || v === null || v === false) continue;
    if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "class") el.className = v;
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  for (const c of children.flat()) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}

const humanize = (k) => String(k).replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
const clone = (v) => JSON.parse(JSON.stringify(v));
let uid = 0;
const nextId = () => `f${++uid}`;

function blankLike(v) {
  if (Array.isArray(v)) return [];
  if (v && typeof v === "object") {
    const out = {};
    for (const [k, x] of Object.entries(v)) out[k] = k.startsWith("_") ? x : blankLike(x);
    return out;
  }
  if (typeof v === "number") return 0;
  if (typeof v === "boolean") return false;
  return "";
}

// --- schema lookup: dotted keys, "*" for any list index or map key -----------------

function schemaFor(path) {
  const schema = ed.schema || {};
  let best = null, bestStars = Infinity;
  for (const [key, spec] of Object.entries(schema)) {
    const parts = key.split(".");
    if (parts.length !== path.length) continue;
    let stars = 0, ok = true;
    for (let i = 0; i < parts.length && ok; i++) {
      if (parts[i] === "*") stars++;
      else if (parts[i] !== String(path[i])) ok = false;
    }
    if (ok && stars < bestStars) { best = spec; bestStars = stars; }
  }
  return best || {};
}

function choicesFor(source) {
  if (Array.isArray(source)) return source;
  const d = ed.data || {};
  switch (source) {
    case "variants": return ed.name === "profile" ? Object.keys(d.variants || {}) : (ed.context.variants || []);
    case "roles": return (d.roles || []).map((r) => r.id).filter(Boolean);
    case "creative": return (d.creative || []).map((r) => r.id).filter(Boolean);
    case "links": return Object.keys(d.links || {});
    case "skill_groups": return Object.keys(d.skill_groups || {});
    case "interests": return ["", ...(d.interests || []).map((i) => i.name).filter(Boolean)];
    default: return [];
  }
}

function setAt(path, value) {
  let o = ed.data;
  for (let i = 0; i < path.length - 1; i++) o = o[path[i]];
  o[path[path.length - 1]] = value;
}

// --- the renderer -------------------------------------------------------------------

function field(label, control, help) {
  return h("label", { class: "field" }, h("span", {}, label), control, help ? h("p", { class: "help" }, help) : null);
}

function renderValue(value, path, label) {
  const spec = schemaFor(path);
  const title = spec.label || label;

  if (spec.choices !== undefined) {
    const options = choicesFor(spec.choices);
    if (spec.multi) {
      const current = Array.isArray(value) ? value : [];
      const all = [...new Set([...options, ...current])];
      return h("div", { class: "field" }, h("span", {}, title),
        h("div", { class: "choices" }, all.length ? all.map((opt) => h("label", {},
          h("input", {
            type: "checkbox", checked: current.includes(opt),
            onchange: (e) => {
              const now = new Set(Array.isArray(getValue(path)) ? getValue(path) : []);
              e.target.checked ? now.add(opt) : now.delete(opt);
              setAt(path, all.filter((x) => now.has(x)));
            },
          }), opt || "(none)")) : h("span", { class: "help" }, "Nothing to choose from yet.")));
    }
    const all = [...new Set([...options, ...(value === undefined ? [] : [value])])];
    return field(title, h("select", { onchange: (e) => setAt(path, e.target.value) },
      all.map((opt) => h("option", { value: opt, selected: opt === value }, opt === "" ? "(none)" : opt))));
  }

  if (spec.boolstr) {
    return field(title, h("input", {
      type: "text", value: String(value ?? ""),
      oninput: (e) => {
        const t = e.target.value.trim();
        setAt(path, t === "true" ? true : t === "false" ? false : t);
      },
    }));
  }

  if (typeof value === "boolean") {
    return h("label", { class: "field check" },
      h("input", { type: "checkbox", checked: value, onchange: (e) => setAt(path, e.target.checked) }),
      h("span", {}, title));
  }

  if (typeof value === "number") {
    return field(title, h("input", {
      type: "number", value: String(value), step: "any",
      oninput: (e) => { const n = Number(e.target.value); if (e.target.value !== "" && !Number.isNaN(n)) setAt(path, n); },
    }));
  }

  if (Array.isArray(value)) {
    const tpl = spec.template;
    const ofObjects = value.some((x) => x && typeof x === "object") || (tpl && typeof tpl === "object" && !Array.isArray(tpl));
    return ofObjects ? renderObjectList(value, path, title, spec) : renderLines(value, path, title);
  }

  if (value && typeof value === "object") {
    return spec.map ? renderMap(value, path, title, spec) : renderObject(value, path, title);
  }

  const text = value === null || value === undefined ? "" : String(value);
  const long = spec.long || text.length > 80 || text.includes("\n");
  return field(title, long
    ? h("textarea", { oninput: (e) => setAt(path, e.target.value) }, text)
    : h("input", { type: "text", value: text, oninput: (e) => setAt(path, e.target.value) }));
}

function getValue(path) {
  let o = ed.data;
  for (const k of path) o = o?.[k];
  return o;
}

function renderLines(value, path, title) {
  return field(`${title} (one per line)`, h("textarea", {
    oninput: (e) => setAt(path, e.target.value.split("\n").map((s) => s.trim()).filter(Boolean)),
  }, value.join("\n")));
}

function children(obj, path) {
  return Object.entries(obj)
    .filter(([k]) => !k.startsWith("_"))
    .map(([k, v]) => renderValue(v, [...path, k], humanize(k)));
}

function noteOf(obj) {
  return obj._note || obj._comment || obj._read_me || "";
}

function renderObject(obj, path, title) {
  const note = noteOf(obj);
  return h("details", { class: "group", open: path.length <= 1 },
    h("summary", {}, title),
    h("div", { class: "body" }, note ? h("p", { class: "help" }, note) : null, children(obj, path)));
}

function itemLabel(item, i) {
  const name = item && (item.name || item.title || item.id || item.degree || item.country || item.text);
  return name ? `${i + 1}. ${String(name).slice(0, 70)}` : `${i + 1}. (new)`;
}

function renderObjectList(list, path, title, spec) {
  const move = (i, j) => { const [x] = list.splice(i, 1); list.splice(j, 0, x); redraw(); };
  const items = list.map((item, i) => {
    const p = [...path, i];
    const tools = h("div", { class: "item-tools" },
      h("button", { type: "button", class: "btn", disabled: i === 0, onclick: () => move(i, i - 1) }, "Move up"),
      h("button", { type: "button", class: "btn", disabled: i === list.length - 1, onclick: () => move(i, i + 1) }, "Move down"),
      h("button", { type: "button", class: "btn", onclick: () => { list.splice(i, 1); redraw(); } }, "Remove"));
    const body = item && typeof item === "object" ? children(item, p) : [renderValue(item, p, "Value")];
    const note = item && typeof item === "object" ? noteOf(item) : "";
    return h("details", { class: "group" }, h("summary", {}, itemLabel(item, i)),
      h("div", { class: "body" }, note ? h("p", { class: "help" }, note) : null, body, tools));
  });
  const add = h("button", {
    type: "button", class: "btn add",
    onclick: () => { list.push(spec.template !== undefined ? clone(spec.template) : blankLike(list[0] ?? "")); redraw(); },
  }, "Add one");
  return h("details", { class: "group", open: path.length <= 1 }, h("summary", {}, `${title} (${list.length})`),
    h("div", { class: "body" }, items, h("div", {}, add)));
}

function renderMap(obj, path, title, spec) {
  const keys = Object.keys(obj).filter((k) => !k.startsWith("_"));
  const rename = (oldKey, newKey) => {
    newKey = newKey.trim();
    if (!newKey || newKey === oldKey) return;
    if (newKey in obj) { toast(`There is already one called ${newKey}.`); redraw(); return; }
    const rebuilt = {};
    for (const [k, v] of Object.entries(obj)) rebuilt[k === oldKey ? newKey : k] = v;
    for (const k of Object.keys(obj)) delete obj[k];
    Object.assign(obj, rebuilt);
    redraw();
  };
  const entries = keys.map((k) => {
    const p = [...path, k];
    const v = obj[k];
    const inner = v && typeof v === "object" && !Array.isArray(v) ? children(v, p) : [renderValue(v, p, "Value")];
    return h("details", { class: "group" }, h("summary", {}, k),
      h("div", { class: "body" },
        field("Name", h("input", { type: "text", value: k, onchange: (e) => rename(k, e.target.value) })),
        inner,
        h("div", { class: "item-tools" },
          h("button", { type: "button", class: "btn", onclick: () => { delete obj[k]; redraw(); } }, "Remove"))));
  });
  const add = h("button", {
    type: "button", class: "btn add",
    onclick: () => {
      let n = 1; while (`new_${n}` in obj) n++;
      const first = keys.length ? obj[keys[0]] : "";
      obj[`new_${n}`] = spec.template !== undefined ? clone(spec.template) : blankLike(first);
      redraw();
    },
  }, "Add one");
  const note = noteOf(obj);
  return h("details", { class: "group", open: path.length <= 1 }, h("summary", {}, `${title} (${keys.length})`),
    h("div", { class: "body" }, note ? h("p", { class: "help" }, note) : null, entries, h("div", {}, add)));
}

// --- screens ------------------------------------------------------------------------

async function openSetup() {
  ed = null;
  previewResult = null;
  const view = $("setup-view");
  let status;
  try {
    status = await api("/api/setup");
  } catch (err) {
    view.replaceChildren(h("div", { class: "empty panel" }, h("h2", {}, "Setup could not load"), h("p", {}, err.message)));
    return;
  }
  const rows = ["profile", "settings", "priorities", "rules"].map((name) => {
    const f = status.files[name];
    const state = !f.exists ? "Not created yet" : f.valid ? "Ready" : "Needs fixing";
    const actions = [];
    if (!f.exists && name === "profile") {
      actions.push(h("button", { type: "button", class: "btn primary", onclick: () => openEditor("profile_start") }, "Start your profile"));
      actions.push(h("button", { type: "button", class: "btn", onclick: () => openEditor("profile") }, "Start from the example"));
    } else {
      actions.push(h("button", { type: "button", class: f.exists ? "btn" : "btn primary", onclick: () => openEditor(name) }, f.exists ? "Edit" : "Create"));
    }
    return h("div", { class: "line" },
      h("div", {}, h("b", {}, f.label), " ", h("span", { class: "muted" }, `(${f.file})`),
        h("div", { class: "sub" }, h("span", { class: `state ${f.exists && f.valid ? "applied" : "pending"}` }, state),
          f.error ? ` - ${f.error}` : "")),
      h("div", { class: "acts" }, actions));
  });
  view.replaceChildren(h("div", { class: "panel" },
    h("h2", {}, "Set up your search"),
    h("p", {}, "Four files hold everything personal: your CV facts, what you are looking for, and the judgement a score cannot make. They stay on this computer."),
    status.warnings.length ? h("div", { class: "note" }, h("b", {}, "Check this: "), status.warnings.join(" ")) : null,
    h("div", { class: "sheet" }, rows)));
}

async function openEditor(name) {
  edError = "";
  edWarnings = [];
  previewResult = null;
  try {
    ed = await api(`/api/setup/${name}`);
  } catch (err) {
    toast(err.message);
    return;
  }
  redraw();
  window.scrollTo(0, 0);
}

function redraw() {
  if (!ed) return;
  const view = $("setup-view");
  const y = window.scrollY;
  const isMd = Array.isArray(ed.sections);
  const intro = ed.name === "profile_start"
    ? "The same questions as cv/init.py. Fill in what is on your CV now; everything can be changed later in the full form."
    : ed.from_example
      ? "This starts from the example. Change what applies to you, then save - nothing is written until you do."
      : "";
  const saveLabel = ed.name === "profile_start" ? "Create my profile" : "Save";
  view.replaceChildren(h("div", { class: "form" },
    h("div", { class: "actions" }, h("button", { type: "button", class: "btn", onclick: openSetup }, "Back to Setup")),
    h("h2", {}, ed.label),
    intro ? h("p", { class: "intro" }, intro) : null,
    edError ? h("div", { class: "errorbox", role: "alert" }, h("b", {}, "Not saved. "), edError) : null,
    edWarnings.length ? h("div", { class: "note" }, h("b", {}, "Saved, with warnings: "), edWarnings.join(" ")) : null,
    isMd ? renderMarkdown() : children(ed.data, []),
    ed.name === "profile" && !ed.from_example ? renderPreview() : null,
    h("div", { class: "savebar" },
      h("button", { type: "button", class: "btn primary", onclick: saveEditor }, saveLabel),
      h("button", { type: "button", class: "btn", onclick: () => openEditor(ed.name) }, "Discard changes"))));
  window.scrollTo(0, y);
}

function renderMarkdown() {
  const sections = ed.sections.map((s, i) => h("details", { class: "group", open: true },
    h("summary", {}, s.heading || "(no heading)"),
    h("div", { class: "body" },
      field("Heading", h("input", { type: "text", value: s.heading, oninput: (e) => { s.heading = e.target.value; } })),
      s.help && s.help !== s.body ? h("details", {}, h("summary", { class: "help" }, "What the example says here"), h("p", { class: "help" }, s.help)) : null,
      field("Your answer", h("textarea", { rows: 8, oninput: (e) => { s.body = e.target.value; } }, s.body)),
      h("div", { class: "item-tools" }, h("button", { type: "button", class: "btn", onclick: () => { ed.sections.splice(i, 1); redraw(); } }, "Remove this section")))));
  return [
    field("Introduction (optional)", h("textarea", { rows: 4, oninput: (e) => { ed.preamble = e.target.value; } }, ed.preamble)),
    ...sections,
    h("div", {}, h("button", { type: "button", class: "btn add", onclick: () => { ed.sections.push({ heading: "New section", body: "", help: "" }); redraw(); } }, "Add a section")),
  ];
}

function renderPreview() {
  const variants = Object.keys(ed.data.variants || {});
  if (!variants.length) return null;
  const select = h("select", { id: "preview-variant" }, variants.map((v) => h("option", { value: v }, v)));
  const result = previewResult
    ? h("div", { class: "line" },
        h("div", {}, h("b", {}, previewResult.variant),
          h("div", { class: "sub" }, previewResult.pdf
            ? `${previewResult.pages ? `${previewResult.pages} page${previewResult.pages === 1 ? "" : "s"}, ` : ""}PDF and Word`
            : "Word document only - page count not checked",
            previewResult.warnings.length ? `. ${previewResult.warnings.join(" ")}` : "")),
        h("div", { class: "acts" },
          h("button", { type: "button", class: "btn", onclick: () => openFile(previewResult.pdf || previewResult.docx, false) }, "Open"),
          h("button", { type: "button", class: "btn", onclick: () => openFile(previewResult.docx, true) }, "Show in folder")))
    : null;
  return h("details", { class: "group", open: true }, h("summary", {}, "Preview a CV"),
    h("div", { class: "body" },
      h("p", { class: "help" }, "Builds one CV from the saved profile - save first to see your latest changes."),
      h("div", { class: "actions" }, select,
        h("button", { type: "button", class: "btn", onclick: () => previewCv(select.value) }, "Preview CV")),
      result));
}

async function previewCv(variant) {
  const result = await runTask(() => api("/api/setup/preview", { variant }), "preview");
  if (result) {
    previewResult = result;
    redraw();
  }
}

async function saveEditor() {
  edError = "";
  edWarnings = [];
  try {
    if (ed.name === "profile_start") {
      await api("/api/setup/profile/start", { answers: ed.data });
      toast("Profile created");
      await openEditor("profile");
      await refresh();
      return;
    }
    const body = Array.isArray(ed.sections) ? { preamble: ed.preamble, sections: ed.sections } : { data: ed.data };
    const r = await api(`/api/setup/${ed.name}`, body);
    edWarnings = r.warnings || [];
    ed.from_example = false;
    toast(`Saved ${r.saved}`);
    await refresh();
  } catch (err) {
    edError = err.message;
  }
  redraw();
}
