'use strict';

async function api(path, body, csrf = state.csrf) {
  const options = { credentials: "same-origin", headers: {} };
  if (body !== undefined) {
    options.method = "POST";
    options.headers = {
      "Content-Type": "application/json",
      "X-Nixlab-CSRF": csrf,
    };
    options.body = JSON.stringify(body);
  }
  const r = await fetch("/api/" + path, options);
  const result = await r.json();
  if (!r.ok) {
    if (r.status === 401 && !["login", "enrollment", "bootstrap", "bootstrap/verify", "bootstrap/claim", "recovery/request", "recovery/status", "recovery/reset"].includes(path))
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

async function claimCertificate() {
  const response = await fetch("/api/browser-certificate/download", {
    method: "POST", credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-Nixlab-CSRF": state.csrf },
    body: "{}",
  });
  if (!response.ok) {
    const error = await response.json();
    throw Error(error.error || "Certificate download unavailable; refresh the identity list");
  }
  return new Uint8Array(await response.arrayBuffer());
}
