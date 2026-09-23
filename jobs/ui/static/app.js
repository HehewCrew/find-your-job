// jobHunt - the page. A thin renderer over the local API: every action goes to the
// server, then the page re-reads state, so the sheet and briefs on disk stay the truth.
"use strict";

const TOKEN = document.querySelector('meta[name="fyj-token"]').content;
const STEPS = ["Scrape", "Review", "Build", "Apply", "Close"];
const PHASE_STEP = { scrape: 0, review: 1, apply: 3, close: 4 };
const ENGINE = { word: "PDFs via Word", libreoffice: "PDFs via LibreOffice", none: "No PDF program found" };
const LIBREOFFICE = "https://www.libreoffice.org/download/download/";

let S = null;            // /api/state
let step = 0;            // step shown
let pinned = false;      // true once the user (or a finished build) chose the step
let leads = [];
let sel = 0;
let lastBuild = null;    // result of the last pick, for failed rows
let lastScrape = null;
let verdict = null;      // last paste verdict
let following = null;    // EventSource of the running task
let firstLoad = true;    // opens Setup instead of Today when nothing is set up yet

const $ = (id) => document.getElementById(id);
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

async function api(path, body) {
  const opts = body === undefined ? {} : {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-FYJ-Token": TOKEN },
    body: JSON.stringify(body),
  };
  const r = await fetch(path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok) {
    const e = new Error(data.error || `The server answered ${r.status}.`);
    e.data = data;
    e.status = r.status;
    throw e;
  }
  return data;
}

function toast(msg) {
  const t = $("toast");
  t.textContent = msg;
  t.hidden = false;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (t.hidden = true), 3200);
}

function fail(err) {
  toast(err.message || String(err));
}

// --- the running task ---------------------------------------------------------------

function progressLine(html, failed = false) {
  const p = $("progress");
  p.classList.toggle("failed", failed);
  p.innerHTML = html;
}

function follow() {
  // Resolves with the task's result, rejects with its failure message.
  return new Promise((resolve, reject) => {
    if (following) following.close();
    const es = new EventSource("/api/events?since=0");
    following = es;
    let headline = "Working…";
    es.onmessage = (msg) => {
      const e = JSON.parse(msg.data);
      if (e.type === "progress") {
        if (!e.detail) headline = e.message.trim();
        const bar = e.total ? `<span class="bar"><i style="width:${(100 * e.done) / e.total}%"></i></span>` : "";
        const tick = e.detail ? `<span class="tick">${esc(e.message)}</span>` : "";
        progressLine(`<span>${esc(headline)}</span>${bar}${tick}`);
      } else {
        es.close();
        following = null;
        if (e.type === "done") {
          progressLine("");
          resolve(e.result);
        } else {
          progressLine(`${esc(e.message)}`, true);
          reject(new Error(e.message));
        }
      }
    };
    es.onerror = () => {
      if (following === es) {
        es.close();
        following = null;
        reject(new Error("Lost touch with the server. Is the window running it still open?"));
      }
    };
  });
}

async function runTask(start, kind) {
  try {
    await start();
  } catch (err) {
    fail(err);
    return null;
  }
  S.task = kind;
  render();
  try {
    return await follow();
  } catch (err) {
    return null;
  } finally {
    await refresh();
  }
}

// --- state --------------------------------------------------------------------------

async function refresh() {
  try {
    S = await api("/api/state");
  } catch (err) {
    progressLine(esc(err.message), true);
    return;
  }
  if (!pinned) step = PHASE_STEP[S.phase] ?? 0;
  const engine = $("engine");
  engine.classList.toggle("none", S.pdf_engine === "none");
  $("engine-text").textContent = ENGINE[S.pdf_engine] || S.pdf_engine;
  const banner = $("banner");
  if (S.settings_error) {
    banner.innerHTML = `<b>Your settings could not be read.</b> ${esc(S.settings_error)}`;
    banner.hidden = false;
  } else if (S.example_settings) {
    banner.innerHTML = '<b>Leads are ranked for the example person.</b> Describe your own search in Setup. <button type="button" class="btn" data-tab="setup">Open Setup</button>';
    banner.hidden = false;
  } else {
    banner.hidden = true;
  }
  $("setup-flag").hidden = !S.setup_missing.length;
  if (firstLoad) {
    firstLoad = false;
    if (S.setup_missing.includes("profile") || S.setup_missing.includes("settings")) showTab("setup");
  }
  if (S.task && !following) follow().catch(() => {}).finally(refresh);
  await render();
}

function goto(i, pin = true) {
  step = i;
  pinned = pin;
  render();
}

// --- step bar -----------------------------------------------------------------------

function renderSteps() {
  const today = S.sheet_date === S.today;
  const total = S.leads.take + S.leads.skip + S.leads.pending;
  const briefs = S.briefs.pending + S.briefs.applied + S.briefs.aborted;
  const outs = [
    today ? `${plural(total, "lead")} found` : "",
    today ? `${S.leads.take} taken, ${S.leads.skip} dropped` : "",
    S.cvs ? `${plural(S.cvs, "CV")} built` : "",
    briefs ? `${S.briefs.applied} of ${briefs} sent` : "",
    "",
  ];
  const reached = PHASE_STEP[S.phase] ?? 0;
  $("steps").innerHTML = STEPS.map((name, i) => {
    const cls = i === step ? "current" : i < reached ? "done" : i > reached ? "ahead" : "";
    return `<button type="button" class="step ${cls}" role="tab" aria-selected="${i === step}" data-step="${i}">
      <span class="name">${name}</span>
      <span class="out num">${esc(outs[i])}</span></button>`;
  }).join("");
}

// --- views --------------------------------------------------------------------------

function renderScrape() {
  const busy = Boolean(S.task);
  const today = S.sheet_date === S.today;
  const total = S.leads.take + S.leads.skip + S.leads.pending;
  const report = lastScrape
    ? `<p>Fetched ${plural(lastScrape.raw, "posting")}${lastScrape.problems.length ? `; ${plural(lastScrape.problems.length, "source")} did not answer and ${lastScrape.problems.length === 1 ? "was" : "were"} skipped` : ""}. Leads already in your applications are left out.</p>`
    : "";
  $("view").innerHTML = today
    ? `<div class="empty panel"><h2>Today's scrape found ${plural(total, "lead")}</h2>${report}
        <div class="actions"><button type="button" class="btn primary" data-goto="1">Review the leads</button>
        <button type="button" class="btn" id="scrape" ${busy ? "disabled" : ""}>Run it again</button></div>
        <p>Running it again replaces today's sheet, and the ticks on it.</p></div>`
    : `<div class="empty panel"><h2>No sheet for today yet</h2>
        <p>The scrape reads the public job boards, keeps what fits your settings, and ranks it. It takes a minute or two.</p>
        <button type="button" class="btn primary" id="scrape" ${busy ? "disabled" : ""}>Run today's scrape</button></div>`;
}

async function renderReview() {
  leads = await api("/api/leads");
  if (S.sheet_date !== S.today || !leads.length) {
    $("view").innerHTML = `<div class="empty panel"><h2>No leads to review</h2>
      <p>${S.sheet_date && S.sheet_date !== S.today ? `The sheet on disk is from ${esc(S.sheet_date)}.` : "There is no sheet for today."} Run today's scrape to find leads.</p>
      <button type="button" class="btn primary" data-goto="0">Go to Scrape</button></div>`;
    return;
  }
  sel = Math.min(sel, leads.length - 1);
  const l = leads[sel];
  const taken = leads.filter((x) => x.mark === "take").length;
  const dropped = leads.filter((x) => x.mark === "skip").length;
  const mark = (m) => (m === "take" ? "[x]" : m === "skip" ? "[-]" : "[ ]");
  $("view").innerHTML = `
    <div class="split">
      <div class="list" role="listbox" aria-label="Leads" id="list">
        ${leads.map((x, i) => `
          <button type="button" class="row ${x.mark}" role="option" aria-selected="${i === sel}" data-i="${i}">
            <span class="mark">${mark(x.mark)}</span>
            <span class="score num">${esc(x.score ?? "")}</span>
            <span class="who"><b>${esc(x.company)}</b>&ensp;${esc(x.title)}</span>
          </button>`).join("")}
      </div>
      <article class="detail review">
        <h2>${esc(l.title)}</h2>
        <p class="where">${esc(l.company)}${l.location ? `, ${esc(l.location)}` : ""}</p>
        <div class="decide">
          <button type="button" class="btn take" aria-pressed="${l.mark === "take"}" data-mark="take">Take <kbd>x</kbd></button>
          <button type="button" class="btn skip" aria-pressed="${l.mark === "skip"}" data-mark="skip">Drop for good <kbd>-</kbd></button>
          <button type="button" class="btn" data-mark="pending">Decide later <kbd>space</kbd></button>
        </div>
        <dl class="facts">
          <dt>Score</dt><dd class="num">${esc(l.score ?? "–")}${l.reasons.length ? `, ${esc(l.reasons.join(", "))}` : ""}</dd>
          <dt>CV</dt><dd>${esc(l.variant || "–")}</dd>
          <dt>You have</dt><dd>${l.matched.length ? l.matched.map((h) => `<span class="chip have">${esc(h)}</span>`).join("") : "Nothing from your skill list"}</dd>
          <dt>Gaps</dt><dd>${l.gaps.length ? l.gaps.map((g) => `<span class="chip">${esc(g)}</span>`).join("") : "None flagged"}</dd>
          ${l.url ? `<dt>Posting</dt><dd><a href="${esc(l.url)}" target="_blank" rel="noopener noreferrer">Open the original</a></dd>` : ""}
        </dl>
        <div class="jd">${esc(l.description || "No description was saved for this lead.")}</div>
      </article>
    </div>
    <div class="foot">
      <span class="tally num">${taken} taken, ${dropped} dropped, ${leads.length - taken - dropped} undecided</span>
      <span class="keys"><kbd>j</kbd><kbd>k</kbd> move</span>
      <button type="button" class="btn primary push" id="build" ${taken && !S.task ? "" : "disabled"}>Build ${plural(taken, "CV")}</button>
    </div>`;
  const row = document.querySelector(`.row[data-i="${sel}"]`);
  if (row) row.scrollIntoView({ block: "nearest" });
}

function engineNote() {
  return S.pdf_engine === "none"
    ? `<div class="note"><b>Page count not checked.</b> Neither Word nor LibreOffice is installed, so these CVs are Word documents only and nothing confirmed they fit on two pages. <a href="${LIBREOFFICE}" target="_blank" rel="noopener noreferrer">Install LibreOffice</a> (free) to get PDFs and the check.</div>`
    : "";
}

function cvButtons(docx, pdf) {
  const open = pdf || docx;
  if (!open) return "";
  return `<button type="button" class="btn" data-open="${esc(open)}">Open</button>
    <button type="button" class="btn" data-reveal="${esc(docx || pdf)}">Show in folder</button>`;
}

async function renderBuild() {
  const cvs = await api("/api/cvs");
  const failed = (lastBuild?.cvs || []).filter((c) => c.error);
  if (!cvs.length && !failed.length) {
    $("view").innerHTML = `<div class="empty panel"><h2>No CVs built yet</h2>
      <p>Take at least one lead on the Review step, then build its CV.</p>
      <button type="button" class="btn" data-goto="1">Back to Review</button></div>`;
    return;
  }
  const built = new Map((lastBuild?.cvs || []).map((c) => [c.company, c]));
  $("view").innerHTML = `${engineNote()}
    <div class="sheet">
      ${cvs.map((c) => {
        const b = built.get(c.company);
        const bits = [c.pdf ? "PDF and Word" : "Word document only"];
        if (b && b.pages) bits.unshift(plural(b.pages, "page"));
        if (b && b.kept < b.wanted) bits.push(b.kept ? `keyword list trimmed to ${b.kept} to fit` : "no room for a keyword list on this CV");
        return `<div class="line"><div><b>${esc(c.company)}</b>${c.title ? `, ${esc(c.title)}` : ""}
          <div class="sub">${esc(bits.join(", "))}</div></div>
          <div class="acts">${cvButtons(c.docx, c.pdf)}</div></div>`;
      }).join("")}
      ${failed.map((c) => `<div class="line failed"><div><b>${esc(c.company)}</b>
          <div class="sub">Not built: ${esc(c.error)}</div></div><div></div></div>`).join("")}
    </div>
    <div class="foot"><button type="button" class="btn primary push" data-goto-free="3">Start applying</button></div>`;
}

async function renderApply() {
  const briefs = await api("/api/briefs");
  if (!briefs.length) {
    $("view").innerHTML = `<div class="empty panel"><h2>Nothing to apply to yet</h2>
      <p>Built CVs land here, one row per posting, until you mark each one.</p>
      <button type="button" class="btn" data-goto="1">Back to Review</button></div>`;
    return;
  }
  const left = briefs.filter((b) => b.state === "pending").length;
  const label = { pending: "Not sent yet", applied: "Applied", aborted: "Not pursued" };
  $("view").innerHTML = `${engineNote()}
    <div class="sheet">
      ${briefs.map((b) => `
        <div class="line"><div><b>${esc(b.company)}</b>, ${esc(b.title)}
          <div class="sub"><span class="state ${esc(b.state)}">${esc(label[b.state] || b.state)}</span>${b.reason ? `, ${esc(b.reason)}` : ""}</div></div>
          <div class="acts">
            ${b.url ? `<a class="btn" href="${esc(b.url)}" target="_blank" rel="noopener noreferrer">Posting</a>` : ""}
            ${b.cv.length ? cvButtons(b.cv.find((p) => p.endsWith(".docx")), b.cv.find((p) => p.endsWith(".pdf"))) : ""}
            <button type="button" class="btn primary" data-apply="${b.index}" ${b.state === "applied" ? "disabled" : ""}>Mark applied</button>
            <button type="button" class="btn" data-abort="${b.index}" ${b.state === "aborted" ? "disabled" : ""}>Not pursuing</button>
          </div></div>`).join("")}
    </div>
    <div class="foot">
      <span class="tally">${left ? `${left} still to decide` : "All decided."}</span>
      <button type="button" class="btn primary push" data-goto="4" ${left ? "disabled" : ""}>Review the close</button>
    </div>`;
}

async function renderClose() {
  let preview;
  try {
    preview = await api("/api/close", { dry_run: true, force: false });
  } catch (err) {
    if (err.status === 409 && err.data.pending) {
      $("view").innerHTML = `<div class="panel"><h2>Some applications are still undecided</h2>
        <p>${esc(err.data.pending.join("; "))}</p>
        <div class="actions"><button type="button" class="btn primary" data-goto="3">Decide them</button>
        <button type="button" class="btn" id="close-force">Close anyway and drop them</button></div></div>`;
    } else {
      $("view").innerHTML = `<div class="empty panel"><h2>Nothing to close yet</h2><p>${esc(err.message)}</p></div>`;
    }
    return;
  }
  const f = preview.filed, d = preview.dropped;
  $("view").innerHTML = `<div class="panel">
    <h2>Closing the day will</h2>
    <p>File ${plural(f.length, "application")} into its own folder with the CV you sent and a page for the form's questions, record ${d.length} as not pursued so they never come back, and add today to your application log.</p>
    <div class="sheet" style="margin-bottom:14px">
      ${f.map((x) => `<div class="line"><div><b>${esc(x.company)}</b>, ${esc(x.title)}<div class="sub">${esc(x.folder)}${x.problems.length ? `, ${esc(x.problems.join("; "))}` : ""}</div></div><span class="state applied">File</span></div>`).join("")}
      ${d.map((x) => `<div class="line"><div><b>${esc(x.company)}</b>, ${esc(x.title)}</div><span class="state aborted">Not pursued</span></div>`).join("")}
    </div>
    <button type="button" class="btn primary" id="close-day">Close the day</button></div>`;
}

async function render() {
  if (!S) return;
  renderSteps();
  try {
    await [renderScrape, renderReview, renderBuild, renderApply, renderClose][step]();
  } catch (err) {
    $("view").innerHTML = `<div class="empty panel"><h2>This step could not load</h2><p>${esc(err.message)}</p></div>`;
  }
}

// --- actions ------------------------------------------------------------------------

async function setMark(m) {
  const l = leads[sel];
  if (!l) return;
  try {
    await api("/api/leads/mark", { url: l.url, mark: m });
  } catch (err) {
    fail(err);
  }
  await refresh();
}

async function scrapeNow() {
  const result = await runTask(() => api("/api/scrape", {}), "scrape");
  if (result) {
    lastScrape = result;
    toast(`Found ${plural(result.leads, "lead")}`);
    goto(1, false);
  }
}

async function buildNow() {
  const result = await runTask(() => api("/api/pick", {}), "build");
  if (result) {
    lastBuild = result;
    const ok = result.cvs.filter((c) => !c.error).length;
    toast(ok === result.cvs.length ? `Built ${plural(ok, "CV")}` : `Built ${ok} of ${result.cvs.length} CVs`);
    goto(2, true);
  }
}

async function markBrief(index, state) {
  let reason = "";
  if (state === "aborted") {
    reason = window.prompt("Why not? (optional, kept in your log)") ?? null;
    if (reason === null) return;
  }
  try {
    await api("/api/briefs/mark", { selectors: [String(index)], state, reason });
    toast(state === "applied" ? "Marked applied" : "Marked not pursuing");
  } catch (err) {
    fail(err);
  }
  await refresh();
}

async function closeDay(force) {
  try {
    const r = await api("/api/close", { dry_run: false, force });
    toast(`Day closed. Filed ${plural(r.filed.length, "application")}.`);
    lastBuild = null;
    pinned = false;
  } catch (err) {
    fail(err);
  }
  await refresh();
}

async function openFile(path, reveal) {
  try {
    await api("/api/open", { path, reveal });
  } catch (err) {
    fail(err);
  }
}

// --- paste --------------------------------------------------------------------------

function showPasteError(msg) {
  const e = $("p-error");
  e.textContent = msg || "";
  e.hidden = !msg;
}

async function assess(ev) {
  ev.preventDefault();
  showPasteError("");
  $("p-assess").disabled = true;
  try {
    verdict = await api("/api/paste/assess", {
      company: $("p-company").value, title: $("p-title").value, url: $("p-url").value,
      location: $("p-location").value, text: $("p-text").value, variant: "",
    });
    renderVerdict();
  } catch (err) {
    showPasteError(err.message);
  } finally {
    $("p-assess").disabled = false;
  }
}

function renderVerdict(built) {
  const v = verdict;
  const list = (title, items) => (items.length ? `<h3>${title}</h3><ul>${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : "");
  const call = { apply: "Apply", consider: "Worth a closer look", skip: "Skip" }[v.decision] || v.decision;
  $("p-verdict").innerHTML = `
    <div class="verdict ${esc(v.decision)}">
      <div class="call">${esc(call)}</div>
      <p class="muted">${esc(v.company)}, ${esc(v.title)}. ${v.source === "rules" ? "Judged by your settings." : `Judged by ${esc(v.source)}, which read the whole posting.`}</p>
      ${list("Blockers", v.blockers)}${list("Why", v.reasons)}${list("Gaps to prepare for", v.gaps)}${list("Questions to ask", v.questions)}
      <h3>CV</h3><p class="muted">${esc(v.variant)}. Matched: ${esc(v.matched.join(", ") || "nothing from your skill list")}. Gaps: ${esc(v.gap_skills.join(", ") || "none flagged")}.</p>
      ${v.disagree ? `<p class="muted">Your settings alone would have said ${esc(v.rules.decision)}.</p>` : ""}
      ${v.llm_error ? `<p class="muted">The LLM could not be reached, so this is your settings' verdict: ${esc(v.llm_error)}</p>` : ""}
    </div>
    ${built ? `<div class="actions">${cvButtons(built.docx, built.pdf)}<button type="button" class="btn" data-goto-tab="today" data-goto-free="3">Go to Apply</button></div>`
      : `<button type="button" class="btn primary" id="p-build" ${S && S.task ? "disabled" : ""}>${v.decision === "skip" ? "Build anyway" : "Build the CV"}</button>`}`;
  $("p-verdict").hidden = false;
}

async function pasteBuild() {
  const force = verdict.decision === "skip";
  const result = await runTask(() => api("/api/paste/build", { id: verdict.id, force }), "paste");
  if (result) {
    toast(`Built the CV for ${verdict.company} and added it to today's list`);
    renderVerdict(result.cv);
  }
}

// --- wiring -------------------------------------------------------------------------

function showTab(name) {
  document.querySelectorAll(".nav button").forEach((b) => b.setAttribute("aria-current", b.dataset.tab === name ? "page" : "false"));
  for (const t of ["today", "paste", "apps", "setup"]) $(`tab-${t}`).hidden = t !== name;
  if (name === "setup") openSetup();
  if (name === "apps") openApps(true);
}

document.addEventListener("click", (ev) => {
  const t = ev.target.closest("button");
  if (!t) return;
  const d = t.dataset;
  if (d.tab) return showTab(d.tab);
  if (d.gotoTab) showTab(d.gotoTab);
  if (d.step !== undefined) return goto(Number(d.step));
  if (d.goto !== undefined) return goto(Number(d.goto));
  if (d.gotoFree !== undefined) return goto(Number(d.gotoFree), false);
  if (d.i !== undefined) { sel = Number(d.i); return render(); }
  if (d.mark) return setMark(d.mark);
  if (d.open) return openFile(d.open, false);
  if (d.reveal) return openFile(d.reveal, true);
  if (d.apply) return markBrief(d.apply, "applied");
  if (d.abort) return markBrief(d.abort, "aborted");
  if (t.id === "scrape") return scrapeNow();
  if (t.id === "build") return buildNow();
  if (t.id === "close-day") return closeDay(false);
  if (t.id === "close-force") return closeDay(true);
  if (t.id === "p-build") return pasteBuild();
});

document.addEventListener("keydown", (ev) => {
  if ($("tab-today").hidden || step !== 1 || !leads.length) return;
  if (ev.target.matches("input, textarea") || ev.ctrlKey || ev.metaKey || ev.altKey) return;
  if (ev.key === "j" || ev.key === "ArrowDown") { sel = Math.min(sel + 1, leads.length - 1); render(); ev.preventDefault(); }
  else if (ev.key === "k" || ev.key === "ArrowUp") { sel = Math.max(sel - 1, 0); render(); ev.preventDefault(); }
  else if (ev.key === "x") setMark("take");
  else if (ev.key === "-") setMark("skip");
  else if (ev.key === " ") { setMark("pending"); ev.preventDefault(); }
});

$("paste-form").addEventListener("submit", assess);
window.addEventListener("focus", () => { if (!following) refresh(); });
refresh();
