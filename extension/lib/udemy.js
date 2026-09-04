/* Talking to Udemy's private API, from inside the page.
 *
 * These calls must run in the page's own context. Udemy sits behind
 * Cloudflare, and a request from the extension's service worker - even with
 * cookies - carries a different Sec-Fetch-Site profile and gets a 403. The
 * content script is same-origin with the tab the user is already signed in
 * to, which is the entire reason this extension exists: no scraping, no
 * stored password, no automated browser.
 *
 * The endpoints mirror notesgen/udemy_fetch.py. They are undocumented and
 * Udemy can change them without notice.
 */
(function (global) {
  "use strict";

  const CURRICULUM = (id) =>
    `https://www.udemy.com/api-2.0/courses/${id}/subscriber-curriculum-items/` +
    `?page_size=1000` +
    `&fields[lecture]=id,title,object_index,asset` +
    `&fields[chapter]=id,title,object_index` +
    `&fields[asset]=captions,title,asset_type` +
    `&fields[caption]=id,locale_id,url,source`;

  const LECTURE = (courseId, lectureId) =>
    `https://www.udemy.com/api-2.0/users/me/subscribed-courses/${courseId}` +
    `/lectures/${lectureId}/?fields[lecture]=asset&fields[asset]=captions` +
    `&fields[caption]=id,locale_id,url,source`;

  const SUBSCRIBED =
    "https://www.udemy.com/api-2.0/users/me/subscribed-courses/" +
    "?page_size=100&fields[course]=id,title,url";

  async function api(url) {
    const res = await fetch(url, {
      credentials: "include",
      headers: { Accept: "application/json, text/plain, */*" },
    });
    if (res.status === 403) {
      throw new Error(
        "Udemy returned 403. Make sure you are signed in and enrolled in this course."
      );
    }
    if (!res.ok) throw new Error(`Udemy returned HTTP ${res.status}`);
    return res.json();
  }

  const slug = () => (location.pathname.match(/\/course\/([^/?#]+)/) || [])[1] || null;

  /** Resolve the numeric course id. Udemy moves this around, so try in order. */
  async function courseId() {
    const body = document.body;
    const fromData =
      body?.dataset?.clpCourseId ||
      document.querySelector("[data-clp-course-id]")?.getAttribute("data-clp-course-id");
    if (fromData) return Number(fromData);

    const app = document.querySelector(".ud-component--course-taking--app");
    if (app?.dataset?.moduleArgs) {
      try {
        const id = JSON.parse(app.dataset.moduleArgs).courseId;
        if (id) return Number(id);
      } catch (_) {}
    }

    const html = document.documentElement.innerHTML;
    const m = html.match(/"course_id":\s*(\d+)/) || html.match(/data-clp-course-id="(\d+)"/);
    if (m) return Number(m[1]);

    // Enrolled users get a different layout than the sales page, so fall back
    // to matching this URL's slug against the courses you are enrolled in.
    const want = slug();
    if (want) {
      let next = SUBSCRIBED;
      while (next) {
        const page = await api(next);
        for (const c of page.results || []) {
          if ((c.url || "").includes(`/course/${want}`)) return Number(c.id);
        }
        next = page.next;
      }
    }

    const lec = location.pathname.match(/\/learn\/lecture\/(\d+)/);
    if (lec) {
      const d = await api(
        `https://www.udemy.com/api-2.0/lectures/${lec[1]}/?fields[lecture]=course`
      );
      if (d?.course?.id) return Number(d.course.id);
    }
    throw new Error("Could not work out which course this is. Open the course page and retry.");
  }

  /** Prefer English captions, then anything - matching udemy_fetch._pick_caption. */
  function pickCaption(captions) {
    if (!captions || !captions.length) return null;
    for (const c of captions) {
      if (String(c.locale_id || "").toLowerCase().startsWith("en")) return c.url || null;
    }
    return captions[0].url || null;
  }

  function courseTitle() {
    const el =
      document.querySelector('[data-purpose="course-title"]') ||
      document.querySelector("h1");
    return (el?.textContent || document.title.split("|")[0] || "Udemy course").trim();
  }

  /**
   * The curriculum, with a caption URL attached to every lecture.
   * `onProgress(done, total)` reports the per-lecture caption lookups.
   */
  async function curriculum(id, onProgress) {
    const data = await api(CURRICULUM(id));
    const items = data.results || [];
    const lectures = items.filter((i) => i._class === "lecture");
    let done = 0;

    for (const item of items) {
      if (item._class !== "lecture") continue;
      let captions = item.asset ? item.asset.captions : undefined;
      if (captions === undefined || captions === null) {
        // The curriculum call usually inlines captions; ask per lecture if not.
        try {
          const detail = await api(LECTURE(id, item.id));
          captions = (detail.asset || {}).captions || [];
        } catch (_) {
          captions = [];
        }
      }
      item.captionUrl = pickCaption(captions);
      done += 1;
      if (onProgress) onProgress(done, lectures.length);
    }
    return items;
  }

  global.NotesgenUdemy = { api, slug, courseId, courseTitle, curriculum, pickCaption };
})(typeof window !== "undefined" ? window : globalThis);
