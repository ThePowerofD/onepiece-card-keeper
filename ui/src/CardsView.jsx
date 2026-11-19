import { useEffect, useState } from "react";
import { api } from "./api.js";
import CardImage from "./CardImage.jsx";

const PAGE = 60;
const COLORS = ["Red", "Green", "Blue", "Purple", "Black", "Yellow"];
const CATEGORIES = ["Leader", "Character", "Event", "Stage"];

// The grid: one tile per card (not per art - the arts are on the detail).
// Starts on "only cards I own", which is the collection.
export default function CardsView({ version, onOpen }) {
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
      </div>

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
