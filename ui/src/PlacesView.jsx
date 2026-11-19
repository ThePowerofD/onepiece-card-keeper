import { useEffect, useState } from "react";
import { api } from "./api.js";

// Every place a copy can be, with its counts - the same numbers as
// `optcg storage list` and `optcg deck list`, and as SAMPLE.md for the frozen
// sample. Open one to see what it holds, art by art (`optcg storage show`).
// Create a storage place (M2) - `optcg storage add "<name>" --type <type>`.
function NewPlace({ onCreated }) {
  const [types, setTypes] = useState([]);
  const [name, setName] = useState("");
  const [kind, setKind] = useState("binder");
  const [error, setError] = useState(null);
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    // 'loose' and 'loan' are built in: just-loose already exists, and a loan
    // place is made when you lend a card.
    api.storageTypes()
      .then((t) => setTypes(t.filter((x) => x !== "loose")))
      .catch(() => setTypes([]));
  }, []);

  async function create(e) {
    e.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const place = await api.newPlace({ name: name.trim(), kind });
      setName("");
      onCreated({ name: place.name, kind: place.kind });
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <form className="new-place" onSubmit={create}>
      <label>
        New place:{" "}
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Binder B"
               aria-label="new place name" />
      </label>
      <select value={kind} onChange={(e) => setKind(e.target.value)} aria-label="type">
        {types.map((t) => <option key={t}>{t}</option>)}
      </select>
      <button className="small" disabled={!name.trim() || saving}>Create</button>
      {error && <p className="error" role="alert">{error}</p>}
    </form>
  );
}

export default function PlacesView({ version, onOpen, onChanged }) {
  const [places, setPlaces] = useState(null);
  const [chosen, setChosen] = useState(null); // {name, kind}
  const [contents, setContents] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    api.places().then(setPlaces).catch((e) => setError(e.message));
  }, [version]);

  useEffect(() => {
    if (!chosen) return;
    setContents(null);
    api.placeContents(chosen.name, chosen.kind).then(setContents).catch((e) => setError(e.message));
  }, [chosen, version]);

  if (error) return <p className="error" role="alert">{error}</p>;
  if (!places) return <p>Loading…</p>;

  return (
    <section className="places" aria-label="places">
      <NewPlace onCreated={(place) => { onChanged(); setChosen(place); }} />
      <table>
        <thead>
          <tr><th>Place</th><th>Type</th><th className="num">Cards</th><th className="num">Copies</th></tr>
        </thead>
        <tbody>
          {places.map((p) => {
            const isChosen = chosen && chosen.name === p.name && chosen.kind === p.kind;
            return (
              <tr key={`${p.kind}|${p.name}|${p.deck_id ?? ""}`} className={isChosen ? "chosen" : ""}>
                <td>
                  {p.holds_copies ? (
                    <button className="link" onClick={() => setChosen({ name: p.name, kind: p.kind })}>
                      {p.name}
                    </button>
                  ) : (
                    <span title="a wishlist is a plan and holds no copies">{p.name}</span>
                  )}
                </td>
                <td>{p.kind === "deck" ? (p.physical ? "deck" : "wishlist") : p.kind}</td>
                <td className="num">{p.cards}</td>
                <td className="num">{p.copies}</td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {chosen && (
        <div className="contents">
          <h2>
            {chosen.name} <span className="muted">{chosen.kind}</span>
          </h2>
          {!contents && <p>Loading…</p>}
          {contents && (
            <>
              <p className="count">{contents.copies} copies, {contents.cards} card(s)</p>
              <table>
                <thead>
                  <tr><th>Card</th><th>Name</th><th>Art</th><th className="num">Copies</th></tr>
                </thead>
                <tbody>
                  {contents.rows.map((r) => (
                    <tr key={`${r.base_card_id}|${r.card_image_id}`}>
                      <td>
                        <button className="link" onClick={() => onOpen(r.base_card_id)}>
                          {r.base_card_id}
                        </button>
                      </td>
                      <td>{r.name}</td>
                      <td className={r.card_image_id ? "" : "todo"}>{r.card_image_id || "unsorted"}</td>
                      <td className="num">{r.quantity}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </>
          )}
        </div>
      )}
    </section>
  );
}
