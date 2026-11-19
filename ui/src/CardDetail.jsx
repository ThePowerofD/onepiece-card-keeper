import { useEffect, useState } from "react";
import { api } from "./api.js";
import CardImage from "./CardImage.jsx";

function artLabel(printing) {
  if (!printing) return "unsorted";
  return printing.printing_variant ? printing.printing_variant : "base";
}

function placeLabel(place) {
  return `${place.container ?? place.name} (${place.kind})`;
}

// One card, opened over the grid: every art side by side with how many you own
// of each, where every copy is, and what you can do with a pile: record its art
// (M1, D-044) or move it somewhere else (M2).
export default function CardDetail({ cardId, onClose, onChanged }) {
  const [card, setCard] = useState(null);
  const [places, setPlaces] = useState([]);
  const [error, setError] = useState(null);

  useEffect(() => {
    setCard(null);
    setError(null);
    api.card(cardId).then(setCard).catch((e) => setError(e.message));
    api.places().then(setPlaces).catch(() => setPlaces([]));
  }, [cardId]);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const ownedOf = (imageId) =>
    card ? card.arts.filter((a) => a.card_image_id === imageId).reduce((n, a) => n + a.quantity, 0) : 0;

  function saved(updated) {
    setCard(updated);
    api.places().then(setPlaces).catch(() => {});
    onChanged();
  }

  return (
    <div className="overlay" onClick={onClose}>
      <div className="detail" role="dialog" aria-modal="true" aria-label={card ? card.name : cardId}
           onClick={(e) => e.stopPropagation()}>
        <button className="close" onClick={onClose} aria-label="close">×</button>
        {error && <p className="error" role="alert">{error}</p>}
        {!card && !error && <p>Loading…</p>}
        {card && (
          <>
            <h2>
              {card.name} <span className="muted">{card.base_card_id}</span>
            </h2>
            <p className="totals">
              You own <strong>{card.total_owned}</strong>
              {card.total_owned > 0 && (
                <>
                  {" "}· {card.in_decks} in decks · {card.not_in_decks} elsewhere ·{" "}
                  <span className={card.unsorted ? "todo" : ""}>{card.unsorted} art not set</span>
                </>
              )}
            </p>

            <h3>Arts</h3>
            <ul className="printings">
              {card.printings.map((p) => (
                <li key={p.card_image_id}>
                  <CardImage src={p.image_url} alt={`${card.name} ${artLabel(p)}`}
                             label={p.card_image_id} size="large" />
                  <span className="art-name">{artLabel(p)}</span>
                  <span className="muted">{p.printing_kind || ""}</span>
                  <span>own {ownedOf(p.card_image_id)}</span>
                </li>
              ))}
            </ul>

            {card.holdings.length > 0 && (
              <>
                <h3>Where your copies are</h3>
                <ul className="holdings">
                  {card.holdings.map((h) => (
                    <Holding key={`${h.kind}|${h.container}|${h.card_image_id}`}
                             card={card} holding={h} places={places} onSaved={saved} />
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

// One pile: N copies of one art in one place. Two things can be done with it:
//  - Set art / Change art: click the art they are, say how many. The total in
//    the place never changes, only how it's split by art (D-030).
//  - Move: to another place. Out of (or into) a physical deck, the deck's list
//    changes with it - a deck is exactly what's in it (D-041).
// The server refuses anything impossible and writes nothing; its message shows.
function Holding({ card, holding, places, onSaved }) {
  const [mode, setMode] = useState(null); // null | "art" | "move"
  const [target, setTarget] = useState(null); // an art id, or a place key
  const [count, setCount] = useState(holding.quantity);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  // After a save the pile is smaller (or gone): start over.
  useEffect(() => {
    setCount(holding.quantity);
    setTarget(null);
    setMode(null);
  }, [holding.quantity]);

  const current = card.printings.find((p) => p.card_image_id === holding.card_image_id);
  const artChoices = card.printings.filter((p) => p.card_image_id !== holding.card_image_id);
  const destinations = places.filter(
    (p) => p.holds_copies && !(p.name === holding.container && p.kind === holding.kind));

  function toggle(next) {
    setMode(mode === next ? null : next);
    setTarget(null);
    setError(null);
  }

  async function save() {
    setSaving(true);
    setError(null);
    try {
      let result;
      if (mode === "art") {
        result = await api.setArt({
          card: card.base_card_id, quantity: count, art: target,
          place: holding.container, kind: holding.kind,
          from_art: holding.card_image_id, // null = the unsorted ones
        });
      } else {
        const to = destinations.find((p) => `${p.kind}|${p.name}` === target);
        result = await api.move({
          card: card.base_card_id, quantity: count,
          from_place: holding.container, from_kind: holding.kind,
          to_place: to.name, to_kind: to.kind,
          art: holding.card_image_id,
        });
      }
      onSaved(result.card);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  const toDeck = mode === "move" && target && target.startsWith("deck|");

  return (
    <li className="holding">
      <div className="holding-row">
        <span className="qty">{holding.quantity}×</span>
        <span className={holding.card_image_id ? "art-name" : "art-name todo"}>{artLabel(current)}</span>
        <span>in {placeLabel(holding)}</span>
        <span className="actions">
          {artChoices.length > 0 && (
            <button className="small" onClick={() => toggle("art")} aria-expanded={mode === "art"}>
              {mode === "art" ? "Cancel" : holding.card_image_id ? "Change art" : "Set art"}
            </button>
          )}
          {destinations.length > 0 && (
            <button className="small" onClick={() => toggle("move")} aria-expanded={mode === "move"}>
              {mode === "move" ? "Cancel" : "Move"}
            </button>
          )}
        </span>
      </div>

      {mode && (
        <div className="picker">
          {mode === "art" ? (
            <>
              <p>Which art are they? Click it:</p>
              <div className="choices">
                {artChoices.map((p) => (
                  <button key={p.card_image_id}
                          className={target === p.card_image_id ? "choice chosen" : "choice"}
                          onClick={() => setTarget(p.card_image_id)}
                          aria-pressed={target === p.card_image_id}>
                    <CardImage src={p.image_url} alt={artLabel(p)} label={p.card_image_id} size="thumb" />
                    <span>{artLabel(p)}</span>
                  </button>
                ))}
              </div>
            </>
          ) : (
            <p>
              <label>
                Move to:{" "}
                <select value={target || ""} onChange={(e) => setTarget(e.target.value || null)}>
                  <option value="">choose a place…</option>
                  {destinations.map((p) => (
                    <option key={`${p.kind}|${p.name}`} value={`${p.kind}|${p.name}`}>
                      {p.name} ({p.kind})
                    </option>
                  ))}
                </select>
              </label>
            </p>
          )}
          {mode === "move" && holding.kind === "deck" && (
            <p className="note">Taking cards out of a deck makes its list shorter.</p>
          )}
          {toDeck && <p className="note">Adding cards to a deck makes its list longer.</p>}
          <label>
            How many:{" "}
            <select value={count} onChange={(e) => setCount(Number(e.target.value))}>
              {Array.from({ length: holding.quantity }, (_, i) => i + 1).map((n) => (
                <option key={n} value={n}>{n} of {holding.quantity}</option>
              ))}
            </select>
          </label>
          <button className="primary" disabled={!target || saving} onClick={save}>
            {saving ? "Saving…" : "Save"}
          </button>
          {error && <p className="error" role="alert">{error}</p>}
        </div>
      )}
    </li>
  );
}
