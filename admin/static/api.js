'use strict';

async function api(path, body) {
  const options = { credentials: "same-origin", headers: {} };
  if (body !== undefined) {
    options.method = "POST";
    options.headers = {
      "Content-Type": "application/json",
      "X-Nixlab-CSRF": state.csrf,
    };
    options.body = JSON.stringify(body);
  }
  const r = await fetch("/api/" + path, options);
  const result = await r.json();
  if (!r.ok) {
    if (r.status === 401 && path !== "login")
      login("Sign in again to continue.");
    throw Error(result.error || "Request failed");
  }
  return result;
}

function download(name, bytes, type) {
  const blob = new Blob([bytes], { type });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 2000);
}
