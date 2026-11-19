import { useCallback, useEffect, useState } from "react";
import { api } from "./api.js";
import CardsView from "./CardsView.jsx";
import PlacesView from "./PlacesView.jsx";
import CardDetail from "./CardDetail.jsx";

// The whole page: a header with the headline numbers, two tabs (Cards and
// Places), and the card detail that opens over either of them.
export default function App() {
  const [tab, setTab] = useState("cards");
  const [summary, setSummary] = useState(null);
  const [error, setError] = useState(null);
  const [openCard, setOpenCard] = useState(null); // a base_card_id, or null
  // Bumped after every change, so the grid and places reload their numbers.
  const [version, setVersion] = useState(0);

  useEffect(() => {
    api.summary().then(setSummary).catch((e) => setError(e.message));
  }, [version]);

  const changed = useCallback(() => setVersion((v) => v + 1), []);

  return (
    <div className="app">
      <header className="top">
        <h1>Card Keeper</h1>
        {summary && (
          <p className="summary" aria-label="collection totals">
            <strong>{summary.copies}</strong> copies · <strong>{summary.cards}</strong> cards ·{" "}
            <strong>{summary.places}</strong> places
          </p>
        )}
        <nav className="tabs" aria-label="views">
          <button className={tab === "cards" ? "active" : ""} onClick={() => setTab("cards")}>
            Cards
          </button>
          <button className={tab === "places" ? "active" : ""} onClick={() => setTab("places")}>
            Places
          </button>
        </nav>
      </header>

      {error && (
        <p className="error" role="alert">
          Can't reach Card Keeper: {error}. Is <code>optcg web</code> running?
        </p>
      )}

      <main>
        {tab === "cards" ? (
          <CardsView version={version} onOpen={setOpenCard} />
        ) : (
          <PlacesView version={version} onOpen={setOpenCard} />
        )}
      </main>

      {openCard && (
        <CardDetail cardId={openCard} onClose={() => setOpenCard(null)} onChanged={changed} />
      )}
    </div>
  );
}
