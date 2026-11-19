import { useEffect, useState } from "react";
import { api } from "./api.js";
import CardImage from "./CardImage.jsx";

const PAGE = 60;
const COLORS = ["Red", "Green", "Blue", "Purple", "Black", "Yellow"];
const CATEGORIES = ["Leader", "Character", "Event", "Stage"];

// "Add cards" mode (M4): pick where new cards go, then +1 on any tile adds one
// unsorted copy there (`collection add`). The last add can be undone. The art
// is set afterwards on the card's detail, like every imported copy.
function AddBar({ places, target, setTarget, last, onUndo, busy, error }) {
  return (
    <div className="add-bar" role="region" aria-label="adding cards">
      <label>
        New cards go to:{" "}
        <select value={target} onChange={(e) => setTarget(e.target.value)} aria-label="where new cards go">
          {places.map((p) => (
            <option key={`${p.kind}|${p.name}`} value={`${p.kind}|${p.name}`}>
              {p.name} ({p.kind})
            </option>
          ))}
        </select>
      </label>
      <span className="muted">Press +1 on a card for each copy you have.</span>
      {last && (
        <span className="last-add">
          Added 1 {last.name} to {last.place} - you own {last.total} now.{" "}
          <button className="small" disabled={busy} onClick={onUndo}>Undo</button>
        </span>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}

// The grid: one tile per card (not per art - the arts are on the detail).
// Starts on "only cards I own", which is the collection.
export default function CardsView({ version, onOpen, onChanged }) {
  const [adding, setAdding] = useState(false);
  const [places, setPlaces] = useState([]);
  const [target, setTarget] = useState("loose|just-loose");
  const [last, setLast] = useState(null); // the add that Undo would take back
  const [busy, setBusy] = useState(false);
  const [addError, setAddError] = useState(null);
  const [sets, setSets] = useState([]);
  const [filters, setFilters] = useState({
    q: "", set: "", color: "", category: "", owned_only: true,
  });
  const [search, setSearch] = useState(""); // what's typed; `filters.q` follows it
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.sets().then(setSets).catch(() => setSets([]));
  }, []);

  // Wait until typing pauses before searching, instead of on every key.
  useEffect(() => {
    const timer = setTimeout(() => update("q", search.trim()), 250);
    return () => clearTimeout(timer);
  }, [search]);

  useEffect(() => {
    let current = true;
    api
      .cards({ ...filters, limit: PAGE, offset })
      .then((p) => current && (setPage(p), setError(null)))
      .catch((e) => current && setError(e.message));
    return () => {
      current = false; // a slower, older answer must not replace a newer one
    };
  }, [filters, offset, version]);

  function update(key, value) {
    setFilters((f) => (f[key] === value ? f : { ...f, [key]: value }));
    setOffset(0);
  }

  function startAdding() {
    setAdding(true);
    setLast(null);
    setAddError(null);
    update("owned_only", false); // new cards are mostly ones you don't own yet
    api.places()
      .then((all) => setPlaces(all.filter((p) => p.holds_copies)))
      .catch((e) => setAddError(e.message));
  }

  function where() {
    const [kind, ...rest] = target.split("|");
    return { place: rest.join("|"), kind };
  }

  async function plusOne(card) {
    setBusy(true);
    setAddError(null);
    try {
      const r = await api.add({ card: card.base_card_id, quantity: 1, ...where() });
      setLast({ card: card.base_card_id, name: card.name, place: r.place, kind: r.kind,
                total: r.total_owned });
      onChanged();
    } catch (e) {
      setAddError(e.message);
    } finally {
      setBusy(false);
    }
  }

  async function undo() {
    setBusy(true);
    setAddError(null);
    try {
      await api.remove({ card: last.card, quantity: 1, place: last.place, kind: last.kind });
      setLast(null);
      onChanged();
    } catch (e) {
      setAddError(e.message);
    } finally {
      setBusy(false);
    }
  }

  const shownTo = page ? Math.min(offset + PAGE, page.total) : 0;

  return (
    <section aria-label="cards">
      <div className="filters">
        <input
          type="search"
          placeholder="Search a name, e.g. nico robin"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="search by name"
        />
        <select value={filters.set} onChange={(e) => update("set", e.target.value)}
                aria-label="set">
          <option value="">All sets</option>
          {sets.map((s) => <option key={s}>{s}</option>)}
        </select>
        <select value={filters.color} onChange={(e) => update("color", e.target.value)}
                aria-label="color">
          <option value="">All colors</option>
          {COLORS.map((c) => <option key={c}>{c}</option>)}
        </select>
        <select value={filters.category} onChange={(e) => update("category", e.target.value)}
                aria-label="category">
          <option value="">All types</option>
          {CATEGORIES.map((c) => <option key={c}>{c}</option>)}
        </select>
        <label className="check">
          <input
            type="checkbox"
            checked={filters.owned_only}
            onChange={(e) => update("owned_only", e.target.checked)}
          />
          Only cards I own
        </label>
        <button className={adding ? "small active" : "small"} aria-pressed={adding}
                onClick={() => (adding ? setAdding(false) : startAdding())}>
          {adding ? "Done adding" : "Add cards"}
        </button>
      </div>

      {adding && (
        <AddBar places={places} target={target} setTarget={setTarget} last={last}
                onUndo={undo} busy={busy} error={addError} />
      )}

      {error && <p className="error" role="alert">{error}</p>}

      {page && (
        <>
          <p className="count">
            {page.total === 0
              ? "No cards match."
              : `${offset + 1}–${shownTo} of ${page.total} cards`}
          </p>
          <ul className="grid">
            {page.cards.map((card) => (
              <li key={card.base_card_id}>
                <button className="tile" onClick={() => onOpen(card.base_card_id)}
                        title={`${card.name} (${card.base_card_id})`}>
                  <CardImage src={card.image_url} alt={card.name} label={card.base_card_id} />
                  <span className="tile-id">{card.base_card_id}</span>
                  <span className="tile-name">{card.name}</span>
                  <span className="tile-meta">
                    {card.owned > 0 ? (
                      <span className="owned">own {card.owned}</span>
                    ) : (
                      <span className="not-owned">not owned</span>
                    )}
                    {card.printings > 1 && (
                      <span className={card.owned > card.art_recorded ? "arts todo" : "arts"}>
                        {card.printings} arts
                        {card.owned > 0 && ` · ${card.art_recorded}/${card.owned} set`}
                      </span>
                    )}
                  </span>
                </button>
                {adding && (
                  <button className="plus-one" disabled={busy} onClick={() => plusOne(card)}
                          aria-label={`add one ${card.name}`}>
                    +1
                  </button>
                )}
              </li>
            ))}
          </ul>
          {page.total > PAGE && (
            <div className="pager">
              <button disabled={offset === 0} onClick={() => setOffset(offset - PAGE)}>
                ← Previous
              </button>
              <button disabled={shownTo >= page.total} onClick={() => setOffset(offset + PAGE)}>
                Next →
              </button>
            </div>
          )}
        </>
      )}
    </section>
  );
}
