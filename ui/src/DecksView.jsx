import { useEffect, useState } from "react";
import { api } from "./api.js";

// Decks (M3): the list (`optcg deck list`), one deck's cards with owned vs
// missing (`optcg deck show`), and pasting a decklist in (`optcg deck paste`).
export default function DecksView({ version, onOpen, onChanged }) {
  const [decks, setDecks] = useState(null);
  const [chosen, setChosen] = useState(null); // a deck id
  const [detail, setDetail] = useState(null);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.decks().then(setDecks).catch((e) => setError(e.message));
  }, [version]);

  useEffect(() => {
    if (chosen === null) return;
    setDetail(null);
    api.deck(chosen).then(setDetail).catch((e) => setError(e.message));
  }, [chosen, version]);

  if (error) return <p className="error" role="alert">{error}</p>;
  if (!decks) return <p>Loading…</p>;

  return (
    <section className="places" aria-label="decks">
      <p>
        <button className="small" onClick={() => setImporting(!importing)} aria-expanded={importing}>
          {importing ? "Close" : "Import a decklist"}
        </button>
      </p>
      {importing && (
        <DeckImport onImported={(id) => { setImporting(false); onChanged(); setChosen(id); }} />
      )}

      <table>
        <thead>
          <tr><th>Deck</th><th>Kind</th><th>Leader</th><th className="num">Cards</th></tr>
        </thead>
        <tbody>
          {decks.map((d) => (
            <tr key={d.id} className={chosen === d.id ? "chosen" : ""}>
              <td><button className="link" onClick={() => setChosen(d.id)}>{d.name}</button></td>
              <td>{d.is_physical ? "physical" : "wishlist"}</td>
              <td>{d.leader_id || "-"}</td>
              <td className="num">{d.total_cards}</td>
            </tr>
          ))}
        </tbody>
      </table>

      {chosen !== null && (
        <div className="contents">
          {!detail && <p>Loading…</p>}
          {detail && <DeckDetail deck={detail} onOpen={onOpen} />}
        </div>
      )}
    </section>
  );
}

function DeckDetail({ deck, onOpen }) {
  return (
    <>
      <h2>
        {deck.name} <span className="muted">{deck.physical ? "physical" : "wishlist"}</span>
      </h2>
      <p className="count">
        {deck.total_cards} cards
        {deck.physical
          ? ` · ${deck.in_deck_total} in the deck`
          : deck.complete ? " · you own enough to build it" : ` · missing ${deck.missing_total}`}
      </p>
      <table>
        <thead>
          <tr>
            <th>Card</th><th>Name</th>
            <th className="num">Need</th><th className="num">In deck</th>
            <th className="num">Own</th>
            {!deck.physical && <th className="num">Missing</th>}
          </tr>
        </thead>
        <tbody>
          {deck.cards.map((c) => (
            <tr key={c.base_card_id}>
              <td>
                {c.is_leader && <span className="leader" title="leader">L </span>}
                <button className="link" onClick={() => onOpen(c.base_card_id)}>{c.base_card_id}</button>
              </td>
              <td>{c.name}</td>
              <td className="num">{c.needed}</td>
              <td className="num">{c.in_deck}</td>
              <td className="num">{c.owned}</td>
              {!deck.physical && <td className={c.missing ? "num todo" : "num"}>{c.missing}</td>}
            </tr>
          ))}
        </tbody>
      </table>
      {!deck.physical && (
        <p className="note">"Own" counts every copy you have; copies in your physical decks
          can't also be used here.</p>
      )}
    </>
  );
}

// Paste a decklist (Limitless or deckbuilder export). Preview first - the same
// as --dry-run, nothing saved (D-034). If the name is taken, ask: replace it, or
// import as a second deck - never choose for the owner (D-017).
function DeckImport({ onImported }) {
  const [text, setText] = useState("");
  const [name, setName] = useState("");
  const [physical, setPhysical] = useState(true);
  const [result, setResult] = useState(null);
  const [clash, setClash] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function run(preview, onConflict = null) {
    setBusy(true);
    setError(null);
    try {
      const r = await api.importDeck({
        text, name: name.trim(), physical, preview, on_conflict: onConflict,
      });
      setResult(r);
      setClash(false);
      if (!preview) onImported(r.deck_id);
    } catch (e) {
      if (e.code === "name_exists") {
        setClash(true);
        setResult(null);
      } else {
        setError(e.message);
      }
    } finally {
      setBusy(false);
    }
  }

  const ready = text.trim() && name.trim();

  return (
    <div className="picker deck-import">
      <label>
        Deck name:{" "}
        <input value={name} onChange={(e) => { setName(e.target.value); setClash(false); }}
               placeholder="e.g. Purple Doffy" aria-label="deck name" />
      </label>
      <fieldset>
        <label>
          <input type="radio" checked={physical} onChange={() => setPhysical(true)} /> I own it and it's
          built - its cards join my collection
        </label>
        <label>
          <input type="radio" checked={!physical} onChange={() => setPhysical(false)} /> A wishlist -
          show me what I'm missing
        </label>
      </fieldset>
      <textarea value={text} onChange={(e) => setText(e.target.value)} rows={12}
                aria-label="decklist" placeholder={"Paste the list, e.g.\nLeader\n1 Nico Robin (OP09-062)\nCharacter (50)\n4 Nico Robin (OP09-107)\n..."} />
      <div className="buttons">
        <button className="small" disabled={!ready || busy} onClick={() => run(true)}>Preview</button>
        <button className="primary" disabled={!ready || busy} onClick={() => run(false)}>Import</button>
      </div>

      {clash && (
        <div className="note">
          <p>A deck named “{name.trim()}” already exists. What should happen?</p>
          <button className="small" disabled={busy} onClick={() => run(false, "new")}>
            Import as a second deck
          </button>{" "}
          <button className="small" disabled={busy} onClick={() => run(false, "replace")}>
            Replace the old one
          </button>
          <p className="muted">Replacing keeps the deck and rewrites its list; cards the new
            list doesn't use go to just-loose - you still own them. A snapshot is taken first.</p>
        </div>
      )}

      {result && result.preview && (
        <div className="preview">
          <p><strong>Preview - nothing saved.</strong> {result.total_cards} cards across{" "}
            {result.distinct_cards} entries, leader {result.leader_id || "none"}
            {result.is_physical ? `, +${result.collection_credited} copies to your collection` : ""}.</p>
          {result.warnings.length > 0 && (
            <ul>{result.warnings.map((w) => <li key={w} className="todo">{w}</li>)}</ul>
          )}
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  );
}
