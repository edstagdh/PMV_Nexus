// Small fetch wrapper: JSON in/out, readable errors, and a hook so a 401
// anywhere sends the app back to the sign-in screen.
let onUnauthorized = () => {};
export function setUnauthorizedHandler(fn) { onUnauthorized = fn; }

export class ApiError extends Error {
  constructor(message, status) { super(message); this.status = status; }
}

export const OFFLINE_MESSAGE = "Can't reach the server — is it running? Is the network up?";

export async function api(path, { method = "GET", body, signal } = {}) {
  let resp;
  try {
    resp = await fetch(path, {
      method,
      signal,
      credentials: "same-origin",
      headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (err) {
    if (err?.name === "AbortError") throw err;
    // The browser's own wording differs ("Failed to fetch", "NetworkError when attempting
    // to fetch resource.", "Load failed"): one plain message everywhere (2.24.1).
    throw new ApiError(OFFLINE_MESSAGE, 0);
  }
  if (resp.status === 401 && !path.startsWith("/api/auth/")) onUnauthorized();
  const text = await resp.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!resp.ok) {
    const detail = data && typeof data === "object" ? data.detail : null;
    const message = Array.isArray(detail) ? detail.map((d) => d.msg).join("; ") : detail || `Request failed (${resp.status})`;
    throw new ApiError(message, resp.status);
  }
  return data;
}

/** Builds a query string, repeating keys for arrays and skipping empty values. */
export function qs(params) {
  const out = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === "") continue;
    if (Array.isArray(value)) value.forEach((v) => out.append(key, v));
    else out.append(key, String(value));
  }
  return out.toString();
}
