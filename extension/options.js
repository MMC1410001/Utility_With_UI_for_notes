"use strict";
const $ = (id) => document.getElementById(id);

chrome.storage.local.get({ serverUrl: "http://127.0.0.1:8787", token: "" }).then((c) => {
  $("url").value = c.serverUrl;
  $("token").value = c.token;
});

$("save").onclick = async () => {
  await chrome.storage.local.set({
    serverUrl: $("url").value.trim().replace(/\/+$/, ""),
    token: $("token").value.trim(),
  });
  $("saved").textContent = "Saved.";
  setTimeout(() => ($("saved").textContent = ""), 2500);
};
