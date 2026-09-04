"use strict";
/* A view over the worker's state. It owns nothing: closing this window does
 * not stop a capture, and reopening it picks the run back up. */

const $ = (id) => document.getElementById(id);
const OUTPUTS = [
  ["pdf", "PDF"], ["html", "Web page"], ["docx", "Word"],
  ["md", "Markdown"], ["txt", "Text"], ["gdoc", "Google Doc"],
];
const send = (m) => new Promise((r) => chrome.runtime.sendMessage(m, r));
let serverUrl = "http://127.0.0.1:8787";

function renderOutputs(chosen) {
  $("outputs").innerHTML = OUTPUTS.map(([v, label]) =>
    `<label><input type="checkbox" value="${v}" ${chosen.includes(v) ? "checked" : ""}>${label}</label>`
  ).join("");
  $("outputs").addEventListener("change", () => {
    const outputs = [...document.querySelectorAll("#outputs input:checked")].map((i) => i.value);
    chrome.storage.local.set({ outputs });
  });
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

function bytes(n) {
  return n >= 1048576 ? (n / 1048576).toFixed(1) + " MB"
       : n >= 1024 ? Math.round(n / 1024) + " KB" : n + " B";
}

function renderState(s) {
  const running = s.phase && !["done", "failed", "cancelled", "error"].includes(s.phase);
  const finished = ["done", "failed", "cancelled", "error"].includes(s.phase);
  $("progress-box").classList.toggle("hidden", !s.phase);
  $("course-box").classList.toggle("hidden", !!running);

  if (!s.phase) return;
  const nice = { downloading: "Downloading transcripts", uploading: "Sending to notesgen",
                 running: "Generating notes", queued: "Queued", curriculum: "Reading curriculum",
                 done: "Ready", failed: "Failed", cancelled: "Cancelled", error: "Error" };
  $("phase").textContent = nice[s.phase] || s.phase;
  $("ptitle").textContent = s.title || "";
  const pct = s.total ? Math.round((s.done / s.total) * 100) : (s.phase === "running" ? 100 : 0);
  $("fill").style.width = pct + "%";
  $("pcount").textContent = s.total ? `${s.done}/${s.total} lectures` : "";
  $("perr").textContent = s.error || "";

  const files = $("files");
  files.innerHTML = "";
  const r = s.result || {};
  (r.links || []).forEach((l) => {
    files.innerHTML += `<a href="${l.url}" target="_blank"><span>${l.label}</span><span>open</span></a>`;
  });
  (r.artifacts || []).filter((a) => a.download).slice(0, 12).forEach((a) => {
    files.innerHTML += `<a href="${serverUrl}${a.download}?token=${encodeURIComponent(TOKEN)}"
      target="_blank"><span>${a.name}</span><span>${bytes(a.size)}</span></a>`;
  });
  if (finished && r.cost_usd) {
    files.innerHTML += `<div class="muted">This run cost $${r.cost_usd.toFixed(2)}.</div>`;
  }
}

let TOKEN = "";

(async function boot() {
  const cfg = await chrome.storage.local.get({
    serverUrl: "http://127.0.0.1:8787", token: "", outputs: ["pdf", "html"],
  });
  serverUrl = cfg.serverUrl; TOKEN = cfg.token;
  renderOutputs(cfg.outputs);

  const h = await send({ type: "health" });
  if (h && h.ok) {
    serverUrl = h.serverUrl || serverUrl;
    $("dot").className = "dot on";
    $("server").textContent = "notesgen is running";
    $("where").textContent = serverUrl;
    $("main").hidden = false;
    if (!TOKEN) {
      $("where").innerHTML +=
        ` &middot; <span style="color:var(--bad)">no token set - open Settings</span>`;
    }
  } else {
    $("dot").className = "dot off";
    $("server").textContent = "server not found";
    $("setup-hint").hidden = false;
  }

  const tab = await activeTab();
  const onCourse = tab && /^https:\/\/www\.udemy\.com\/course\//.test(tab.url || "");
  if (onCourse) {
    try {
      const info = await chrome.tabs.sendMessage(tab.id, { type: "page-info" });
      if (info) { $("course").textContent = info.title; $("go").disabled = false; }
    } catch (_) {
      $("course").textContent = "Reload the course page, then reopen this.";
    }
  }

  $("go").onclick = async () => {
    $("go").disabled = true;
    const t = await activeTab();
    await chrome.tabs.sendMessage(t.id, { type: "start-capture" });
    setTimeout(refresh, 400);
  };
  $("clear").onclick = async () => { await send({ type: "reset" }); renderState({}); };
  $("retry").onclick = () => location.reload();
  $("open-ui").onclick = (e) => { e.preventDefault(); chrome.tabs.create({ url: serverUrl }); };
  const openOpts = (e) => { e.preventDefault(); chrome.runtime.openOptionsPage(); };
  $("opts1").onclick = openOpts; $("opts2").onclick = openOpts;

  refresh();
  setInterval(refresh, 1200);
})();

async function refresh() {
  const s = await send({ type: "state" });
  if (s) renderState(s);
}
