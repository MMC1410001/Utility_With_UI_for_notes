/* The service worker: caption downloads, server calls, and job tracking.
 *
 * Everything that touches the local server lives here. A content script on an
 * https Udemy page cannot fetch http://127.0.0.1 - Chrome blocks it as mixed
 * content - but the extension's own origin can. Capture state lives in
 * chrome.storage.session rather than in the popup, so closing the popup never
 * interrupts a run.
 */

const DEFAULTS = {
  serverUrl: "http://127.0.0.1:8787",
  token: "",
  outputs: ["pdf", "html"],
};

// Ports to probe when the configured one is not answering. 127.0.0.1, never
// "localhost": uvicorn binds IPv4 and localhost can resolve to ::1 first.
const PROBE_PORTS = [8787, 8788, 8799];

const settings = async () => ({ ...DEFAULTS, ...(await chrome.storage.local.get(DEFAULTS)) });
const setState = (patch) =>
  chrome.storage.session.get({ capture: {} }).then(({ capture }) =>
    chrome.storage.session.set({ capture: { ...capture, ...patch } }));
const getState = () =>
  chrome.storage.session.get({ capture: {} }).then(({ capture }) => capture);

async function badge(text, colour) {
  try {
    await chrome.action.setBadgeText({ text: text || "" });
    if (colour) await chrome.action.setBadgeBackgroundColor({ color: colour });
  } catch (_) {}
}

/* --------------------------------------------------------------- server */
async function call(path, options = {}) {
  const cfg = await settings();
  const res = await fetch(cfg.serverUrl + path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      "X-Notesgen-Token": cfg.token,
      ...(options.headers || {}),
    },
  });
  if (!res.ok) {
    let detail = `HTTP ${res.status}`;
    try { detail = (await res.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  return res.json();
}

async function health() {
  const cfg = await settings();
  const tries = [cfg.serverUrl, ...PROBE_PORTS.map((p) => `http://127.0.0.1:${p}`)];
  for (const base of tries) {
    try {
      const res = await fetch(base + "/api/health", { signal: AbortSignal.timeout(1500) });
      if (!res.ok) continue;
      const body = await res.json();
      if (body.app !== "notesgen") continue;
      if (base !== cfg.serverUrl) await chrome.storage.local.set({ serverUrl: base });
      return { ok: true, serverUrl: base };
    } catch (_) {}
  }
  return { ok: false };
}

/* -------------------------------------------------------------- capture */
async function fetchCaption(url) {
  try {
    const res = await fetch(url, { signal: AbortSignal.timeout(30000) });
    return res.ok ? await res.text() : null;
  } catch (_) {
    return null;          // a missing caption is normal, not fatal
  }
}

/** Caption files can live on a host we did not ask for up front. */
async function ensureHostAccess(urls) {
  const origins = [...new Set(
    urls.filter(Boolean).map((u) => { try { return new URL(u).origin + "/*"; } catch (_) { return null; } })
      .filter(Boolean)
  )];
  if (!origins.length) return true;
  try {
    if (await chrome.permissions.contains({ origins })) return true;
    // Cannot prompt from a worker without a user gesture; record it so the
    // popup can ask, and try anyway - the declared hosts cover most courses.
    await setState({ needsOrigins: origins });
  } catch (_) {}
  return false;
}

async function capture({ course, items }) {
  const lectures = items.filter((i) => i._class === "lecture");
  await setState({
    phase: "downloading",
    title: course.title,
    done: 0,
    total: lectures.length,
    jobId: null,
    error: null,
  });
  await badge("...", "#b4530a");

  await ensureHostAccess(lectures.map((i) => i.captionUrl));

  let done = 0;
  const payload = [];
  for (const item of items) {
    if (item._class !== "lecture") {
      payload.push({ _class: item._class, title: item.title });
      continue;
    }
    const vtt = item.captionUrl ? await fetchCaption(item.captionUrl) : null;
    payload.push({ _class: "lecture", id: item.id, title: item.title, vtt });
    done += 1;
    if (done % 3 === 0 || done === lectures.length) {
      await setState({ done, total: lectures.length });
      await badge(String(Math.round((done / lectures.length) * 100)), "#b4530a");
    }
  }

  // Raw WebVTT goes to the server, which converts it with the same code the
  // command line uses. A JS reimplementation would drift, and the transcript
  // hash is what the manifest resumes on.
  await setState({ phase: "uploading" });
  const imported = await call("/api/courses/import", {
    method: "POST",
    body: JSON.stringify({
      title: course.title,
      slug: course.slug,
      url: course.url,
      format: "vtt",
      items: payload,
    }),
  });

  const cfg = await settings();
  const job = await call("/api/jobs", {
    method: "POST",
    body: JSON.stringify({ source: imported.path, outputs: cfg.outputs }),
  });

  await setState({
    phase: "running",
    jobId: job.job.id,
    courseDir: imported.course_dir,
    lectures: imported.lectures,
    missing: imported.missing,
  });
  chrome.alarms.create("poll", { periodInMinutes: 0.5 });
  poll();
  return { ok: true, jobId: job.job.id, imported };
}

/* ----------------------------------------------------------- job status */
async function poll() {
  const state = await getState();
  if (!state.jobId) return;
  let job;
  try {
    ({ job } = await call(`/api/jobs/${state.jobId}`));
  } catch (_) {
    return;   // server restarted or asleep; try again on the next alarm
  }

  if (["done", "failed", "cancelled"].includes(job.state)) {
    chrome.alarms.clear("poll");
    await setState({ phase: job.state, result: job.result || null, error: job.error || null });
    await badge(job.state === "done" ? "OK" : "!", job.state === "done" ? "#2f7d4f" : "#b3261e");
    try {
      await chrome.notifications.create({
        type: "basic",
        iconUrl: "icons/128.png",
        title: job.state === "done" ? "Notes are ready" : "notesgen run " + job.state,
        message: (job.title || state.title || "Your course") +
                 (job.state === "done" ? " - open the popup to download." : ""),
      });
    } catch (_) {}
  } else {
    await setState({ phase: job.state });
  }
}

chrome.alarms.onAlarm.addListener((a) => { if (a.name === "poll") poll(); });

/* -------------------------------------------------------------- routing */
chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
  (async () => {
    try {
      if (msg.type === "health") return reply(await health());
      if (msg.type === "capture") return reply(await capture(msg));
      if (msg.type === "state") return reply(await getState());
      if (msg.type === "poll") { await poll(); return reply(await getState()); }
      if (msg.type === "progress") {
        await setState({ phase: msg.phase, done: msg.done, total: msg.total, title: msg.title });
        return reply({ ok: true });
      }
      if (msg.type === "reset") {
        await chrome.storage.session.set({ capture: {} });
        await badge("");
        return reply({ ok: true });
      }
      reply({ error: "unknown message" });
    } catch (err) {
      await setState({ phase: "error", error: String(err.message || err) });
      await badge("!", "#b3261e");
      reply({ error: String(err.message || err) });
    }
  })();
  return true;      // keep the channel open for the async reply
});
