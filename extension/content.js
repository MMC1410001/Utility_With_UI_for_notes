/* The button on a Udemy course page, and the capture it kicks off.
 *
 * This script does only what must happen in the page: the Udemy API calls.
 * Caption files and every call to the local notesgen server go through the
 * service worker instead - a page served over https cannot fetch
 * http://127.0.0.1 (Chrome blocks it as mixed content), and the caption files
 * live on a different origin.
 */
(function () {
  "use strict";

  const U = window.NotesgenUdemy;
  let busy = false;

  function button() {
    let el = document.getElementById("notesgen-launch");
    if (el) return el;
    el = document.createElement("button");
    el.id = "notesgen-launch";
    el.type = "button";
    el.innerHTML = `<span class="ng-label">Generate notes</span>`;
    el.addEventListener("click", capture);
    document.body.appendChild(el);
    return el;
  }

  function label(text, spinning) {
    const el = document.getElementById("notesgen-launch");
    if (!el) return;
    el.innerHTML = (spinning ? `<span class="ng-spin"></span>` : "") +
                   `<span class="ng-label">${text}</span>`;
    el.disabled = !!spinning;
  }

  const send = (message) =>
    new Promise((resolve) => chrome.runtime.sendMessage(message, resolve));

  async function capture() {
    if (busy) return;
    busy = true;
    label("Checking server…", true);

    const health = await send({ type: "health" });
    if (!health || !health.ok) {
      label("Server not running", false);
      setTimeout(() => label("Generate notes", false), 4000);
      busy = false;
      return;
    }

    try {
      label("Reading curriculum…", true);
      const id = await U.courseId();
      const title = U.courseTitle();

      const items = await U.curriculum(id, (done, total) => {
        label(`Reading lectures ${done}/${total}`, true);
        send({ type: "progress", phase: "curriculum", done, total, title });
      });

      // Hand off to the worker: it fetches the caption files cross-origin,
      // uploads them, and starts the job. It keeps running if this tab closes.
      label("Downloading transcripts…", true);
      const result = await send({
        type: "capture",
        course: { id, title, slug: U.slug(), url: location.href },
        items: items.map((i) => ({
          _class: i._class,
          id: i.id,
          title: i.title,
          captionUrl: i.captionUrl || null,
        })),
      });

      if (!result || result.error) {
        label(result && result.error ? "Failed" : "Failed", false);
        console.error("[notesgen]", result && result.error);
      } else {
        label("Sent to notesgen", false);
      }
    } catch (err) {
      console.error("[notesgen]", err);
      label(String(err.message || err).slice(0, 40), false);
    } finally {
      setTimeout(() => label("Generate notes", false), 5000);
      busy = false;
    }
  }

  // Let the popup drive a capture too, so the user can pick formats first.
  chrome.runtime.onMessage.addListener((msg, _sender, reply) => {
    if (msg.type === "start-capture") { capture(); reply({ started: true }); }
    if (msg.type === "page-info") {
      reply({ slug: U.slug(), title: U.courseTitle(), url: location.href });
    }
    return true;
  });

  // Udemy is a single-page app, so the button must survive navigation.
  button();
  let last = location.href;
  setInterval(() => {
    if (location.href !== last) { last = location.href; button(); }
  }, 1500);
})();
