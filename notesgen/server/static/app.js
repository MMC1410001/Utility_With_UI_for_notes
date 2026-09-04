"use strict";

const TOKEN = window.NOTESGEN_TOKEN || "";
const $ = (id) => document.getElementById(id);

// Which group each output belongs to. `notes` is implicit - everything is
// built on it and there is no point offering to turn it off.
const LOCAL = ["pdf", "html", "docx", "md", "txt", "diagrams"];
const DRIVE = ["gdoc", "drive-html", "drive-pdf"];

// Mirrors outputs.parse() on the server, so the UI can show what a tick
// silently pulls in rather than surprising the user afterwards.
// Output name -> the folder its files land in, so the result screen can tell
// which groups came from this run.
const KIND_FOR = { md: "export-md", gdoc: "gdocs", "drive-html": "html", "drive-pdf": "pdf" };

const DEPENDS = {
  gdoc: ["docx"],
  "drive-html": ["html"],
  "drive-pdf": ["pdf", "html"],
  pdf: ["html"],
};

const STAGE_ORDER = [
  ["lectures", "Lectures"],
  ["rollups", "Sections"],
  ["index", "Index"],
  ["diagrams", "Diagrams"],
  ["docx", "Word"],
  ["export", "Export"],
  ["publish", "Publish"],
];

let CHOICES = {};
let STATUS = null;
let stream = null;
let jobId = null;

/* ------------------------------------------------------------------ api */
async function api(path, options = {}) {
  const res = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Notesgen-Token": TOKEN,
      ...(options.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

const bytes = (n) =>
  n >= 1048576 ? (n / 1048576).toFixed(1) + " MB"
  : n >= 1024 ? Math.round(n / 1024) + " KB"
  : n + " B";

/* --------------------------------------------------------------- status */
async function loadStatus() {
  try {
    STATUS = await api("/api/status");
  } catch (err) {
    $("strip").innerHTML = `<span class="dot off"></span>server error: ${err.message}`;
    return;
  }
  const s = STATUS;
  const bits = [];
  bits.push(s.provider
    ? `<span><span class="dot on"></span>provider <b>${s.provider}</b></span>`
    : `<span><span class="dot off"></span>no provider configured</span>`);
  if (s.model) bits.push(`<span>model <b>${s.model}</b></span>`);
  bits.push(`<span><span class="dot ${s.mermaid ? "on" : "off"}"></span>diagrams</span>`);
  bits.push(`<span><span class="dot ${s.google.connected ? "on" : "off"}"></span>Google Drive</span>`);
  $("strip").innerHTML = bits.join("");

  const hint = $("drive-hint");
  if (!s.google.connected) {
    hint.innerHTML = `Not connected. <button class="chip" id="gconnect">Connect Google Drive</button>`;
    $("gconnect").onclick = connectGoogle;
  } else {
    hint.textContent = "Connected. Re-running updates the same documents, so links stay valid.";
  }
  renderOutputs();
}

async function connectGoogle() {
  const hint = $("drive-hint");
  hint.textContent = "A browser window will open for Google consent…";
  try {
    await api("/api/google/connect", { method: "POST", body: "{}" });
    // The flow blocks on the job thread; poll until the token lands.
    for (let i = 0; i < 120; i++) {
      await new Promise((r) => setTimeout(r, 1500));
      const g = await api("/api/google/status");
      if (g.valid || g.refreshable) { await loadStatus(); return; }
    }
    hint.textContent = "Timed out waiting for Google consent.";
  } catch (err) {
    hint.textContent = "Could not connect: " + err.message;
  }
}

/* -------------------------------------------------------------- outputs */
function optionRow(name) {
  const why = CHOICES[name] || "";
  const el = document.createElement("label");
  el.className = "opt";
  el.dataset.name = name;
  el.innerHTML =
    `<input type="checkbox" value="${name}">` +
    `<span><span class="name">${name}</span><span class="why">${why}</span></span>`;
  el.querySelector("input").addEventListener("change", refreshDeps);
  return el;
}

function renderOutputs() {
  const local = $("grp-local"), drive = $("grp-drive");
  if (local.childElementCount) return;
  LOCAL.forEach((n) => CHOICES[n] && local.appendChild(optionRow(n)));
  DRIVE.forEach((n) => CHOICES[n] && drive.appendChild(optionRow(n)));
  ["pdf", "html", "docx"].forEach(check);
  refreshDeps();
}

const check = (name) => {
  const box = document.querySelector(`.opt[data-name="${name}"] input`);
  if (box) box.checked = true;
};

function selected() {
  return [...document.querySelectorAll(".opt input:checked")].map((b) => b.value);
}

/** Expand what the user ticked exactly the way the server will. */
function expand(list) {
  const out = new Set(list);
  let grew = true;
  while (grew) {
    grew = false;
    for (const name of [...out]) {
      for (const dep of DEPENDS[name] || []) {
        if (!out.has(dep)) { out.add(dep); grew = true; }
      }
    }
  }
  out.add("notes");
  return out;
}

function refreshDeps() {
  const picked = selected();
  const full = expand(picked);
  document.querySelectorAll(".opt").forEach((el) => {
    const name = el.dataset.name;
    el.classList.toggle("auto", full.has(name) && !picked.includes(name));
  });

  const added = [...full].filter((n) => !picked.includes(n) && n !== "notes");
  $("deps").textContent = added.length
    ? `Also produced, because the above needs it: ${added.join(", ")}.`
    : "Notes are always generated; everything else is built from them.";

  const wantsDrive = picked.some((n) => DRIVE.includes(n));
  const blocked = wantsDrive && STATUS && !STATUS.google.connected;
  $("go").disabled = !!blocked;
  $("go-note").textContent = blocked
    ? "Connect Google Drive first, or untick the Drive outputs."
    : "";
}

/* -------------------------------------------------------------- courses */
async function loadCourses() {
  let data;
  try { data = await api("/api/courses"); } catch (_) { return; }
  if (!data.captured.length) return;
  $("captured-wrap").classList.remove("hidden");
  const box = $("captured");
  box.innerHTML = "";
  data.captured.forEach((c) => {
    const b = document.createElement("button");
    b.className = "chip";
    b.textContent = c.dir;
    b.onclick = () => { $("source").value = c.path; };
    box.appendChild(b);
  });
}

/* ------------------------------------------------------------------ run */
function show(view) {
  ["start", "run", "done"].forEach((v) =>
    $("view-" + v).classList.toggle("hidden", v !== view));
}

function buildStages() {
  const box = $("stages");
  box.innerHTML = "";
  STAGE_ORDER.forEach(([key, label]) => {
    const row = document.createElement("div");
    row.className = "stage";
    row.id = "stage-" + key;
    row.innerHTML =
      `<span class="nm">${label}</span>` +
      `<span class="track"><span class="fill"></span></span>` +
      `<span class="count"></span>`;
    box.appendChild(row);
  });
}

function markStage(key, done, total, finished) {
  const row = $("stage-" + key);
  if (!row) return;
  row.classList.add("active");
  if (total > 0) {
    row.querySelector(".fill").style.width = Math.round((done / total) * 100) + "%";
    row.querySelector(".count").textContent = `${done}/${total}`;
  }
  if (finished) {
    row.classList.add("done");
    row.classList.remove("active");
    row.querySelector(".fill").style.width = "100%";
  }
}

function appendLog(line) {
  const log = $("log");
  const atBottom = log.scrollTop + log.clientHeight >= log.scrollHeight - 30;
  log.textContent += line + "\n";
  if (atBottom) log.scrollTop = log.scrollHeight;
}

async function start() {
  const source = $("source").value.trim();
  if (!source) { $("source").focus(); return; }

  const body = {
    source,
    outputs: selected(),
    workers: Number($("workers").value) || 3,
    force: $("force").checked,
    refetch: $("refetch").checked,
    keep_going: $("keepgoing").checked,
    only: $("only").value.trim() || null,
    sections: $("sections").value.trim()
      ? $("sections").value.split(",").map((s) => s.trim()).filter(Boolean)
      : null,
  };

  $("go").disabled = true;
  let data;
  try {
    data = await api("/api/jobs", { method: "POST", body: JSON.stringify(body) });
  } catch (err) {
    $("go").disabled = false;
    $("go-note").textContent = err.message;
    return;
  }
  $("go").disabled = false;

  jobId = data.job.id;
  $("run-title").textContent = "Working on it…";
  $("log").textContent = "";
  buildStages();
  show("run");
  follow(jobId);
}

function follow(id) {
  if (stream) stream.close();
  stream = new EventSource(`/api/jobs/${id}/events?token=${encodeURIComponent(TOKEN)}`);

  stream.onmessage = () => {};
  ["log", "unit", "stage", "error", "done", "end"].forEach((type) =>
    stream.addEventListener(type, (e) => handle(type, JSON.parse(e.data))));

  stream.onerror = () => {
    // The stream ends normally when the job does; only report a real drop.
    if (stream.readyState === EventSource.CLOSED) poll(id);
  };
}

function handle(type, data) {
  if (type === "log") { appendLog(data.message); return; }
  if (type === "error") { appendLog("error: " + data.message); return; }
  if (type === "unit") { markStage(data.stage, data.done, data.total, false); return; }
  if (type === "stage") {
    if (data.state === "start") markStage(data.stage, 0, data.total || 0, false);
    if (data.state === "done") markStage(data.stage, 1, 1, true);
    return;
  }
  if (type === "done" || type === "end") { finish(); }
}

async function poll(id) {
  try {
    const { job } = await api(`/api/jobs/${id}`);
    if (["done", "failed", "cancelled"].includes(job.state)) finish();
  } catch (_) {}
}

async function finish() {
  if (stream) { stream.close(); stream = null; }
  let job;
  try { ({ job } = await api(`/api/jobs/${jobId}`)); } catch (err) { return; }
  if (!["done", "failed", "cancelled"].includes(job.state)) return;

  const r = job.result || {};
  $("done-title").textContent =
    job.state === "cancelled" ? "Cancelled"
    : job.state === "failed" ? "Failed"
    : r.course || "Done";

  const notes = [];
  if (job.state === "failed") {
    notes.push(`<div class="banner bad">${job.error || "the run failed"}</div>`);
  }
  if (job.state === "cancelled") {
    notes.push(`<div class="banner">Stopped. Everything finished so far was saved —
      running again resumes rather than starting over.</div>`);
  }
  (r.errors || []).forEach((e) => notes.push(`<div class="banner bad">${e}</div>`));
  if (r.failed) {
    notes.push(`<div class="banner">${r.failed} lecture(s) failed.
      Running again retries only those.</div>`);
  }
  if (r.cost_usd) {
    notes.push(`<div class="banner ok">This run spent $${r.cost_usd.toFixed(2)} on model
      calls${r.cost_total_usd ? ` ($${r.cost_total_usd.toFixed(2)} on this course in total)` : ""}.
      Re-exporting to other formats is free.</div>`);
  } else if (r.cost_total_usd) {
    notes.push(`<div class="banner ok">Nothing needed regenerating, so this run was free.
      $${r.cost_total_usd.toFixed(2)} has been spent on this course in total.</div>`);
  }
  $("done-summary").innerHTML = notes.join("") || `<div class="banner ok">Finished.</div>`;

  // Everything the course has ever produced lives on disk, and listing all of
  // it flat buries the handful of files this run was actually about. Group by
  // format, and open only the formats that were asked for this time.
  const files = (r.artifacts || []).filter((a) => a.download);
  const links = r.links || [];
  const produced = new Set((r.outputs || []).map((o) => KIND_FOR[o] || o));

  const parts = [];
  if (links.length) {
    parts.push(`<label class="lbl">In your Drive</label><div class="files">` +
      links.map((l) =>
        `<div class="file"><span class="kind">drive</span>
         <span class="nm">${l.label}</span>
         <a href="${l.url}" target="_blank" rel="noopener">open</a></div>`).join("") +
      `</div>`);
  }

  const groups = new Map();
  files.forEach((f) => {
    if (!groups.has(f.kind)) groups.set(f.kind, []);
    groups.get(f.kind).push(f);
  });

  // Formats from this run first, then the rest.
  const kinds = [...groups.keys()].sort((a, b) =>
    (produced.has(b) ? 1 : 0) - (produced.has(a) ? 1 : 0) || a.localeCompare(b));

  kinds.forEach((kind) => {
    const items = groups.get(kind);
    const fresh = produced.has(kind);
    const rows = items.map((f) => {
      const url = f.download + `?token=${encodeURIComponent(TOKEN)}`;
      const viewable = /\.(html?|pdf|md|txt|png)$/i.test(f.name);
      return `<div class="file"><span class="nm">${f.name}</span>
        <span class="sz">${bytes(f.size)}</span>
        ${viewable ? `<a href="${url}&inline=true" target="_blank" rel="noopener">view</a>` : ""}
        <a href="${url}" download>download</a></div>`;
    }).join("");
    parts.push(
      `<details class="group-files" ${fresh ? "open" : ""}>
         <summary><span class="kind">${kind}</span>
           <span class="gcount">${items.length} file${items.length === 1 ? "" : "s"}</span>
           ${fresh ? `<span class="fresh">this run</span>` : ""}</summary>
         <div class="files">${rows}</div>
       </details>`);
  });

  $("done-files").innerHTML = parts.length
    ? parts.join("")
    : `<p class="muted">Nothing was produced.</p>`;

  show("done");
}

/* ---------------------------------------------------------------- boot */
$("go").onclick = start;
$("again").onclick = () => { show("start"); loadCourses(); };
$("cancel").onclick = async () => {
  if (!jobId) return;
  $("cancel").disabled = true;
  try { await api(`/api/jobs/${jobId}/cancel`, { method: "POST", body: "{}" }); }
  catch (_) {}
  $("cancel").disabled = false;
};

(async function boot() {
  try {
    CHOICES = (await api("/api/outputs")).choices;
  } catch (err) {
    $("strip").textContent = "cannot reach the server: " + err.message;
    return;
  }
  await loadStatus();
  await loadCourses();
})();
