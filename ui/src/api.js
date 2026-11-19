// Talking to `optcg web`. Every call goes to /api on the same address the page
// came from, so there's nothing to configure. In development (`npm run dev`)
// Vite passes /api through to the server on port 8000 (see vite.config.js).

async function request(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...options,
  });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    // The server sends the CLI's own message in `detail` - show it as it is.
    const detail = typeof body.detail === "string" ? body.detail : "the request was refused";
    const error = new Error(detail);
    error.code = body.code; // e.g. "name_exists": a question, not a failure (D-017)
    error.status = response.status;
    throw error;
  }
  return body;
}

function query(params) {
  const clean = Object.entries(params).filter(([, v]) => v !== "" && v !== null && v !== undefined);
  return clean.length ? "?" + new URLSearchParams(clean).toString() : "";
}

const post = (path, body) => request(path, { method: "POST", body: JSON.stringify(body) });

export const api = {
  summary: () => request("/api/summary"),
  sets: () => request("/api/sets"),
  cards: (filters) => request("/api/cards" + query(filters)),
  card: (id) => request(`/api/cards/${encodeURIComponent(id)}`),
  places: () => request("/api/places"),
  placeContents: (name, kind) => request("/api/places/contents" + query({ name, kind })),
  setArt: (body) => post("/api/copies/set-art", body),
  // M2
  storageTypes: () => request("/api/storage-types"),
  newPlace: (body) => post("/api/places", body),
  move: (body) => post("/api/copies/move", body),
  // M4
  add: (body) => post("/api/copies/add", body),
  remove: (body) => post("/api/copies/remove", body), // used only to undo an add
  // M3
  decks: () => request("/api/decks"),
  deck: (id) => request(`/api/decks/${id}`),
  importDeck: (body) => post("/api/decks/import", body),
};
