import { useEffect, useState } from "react";
import { api } from "./api.js";
import CardImage from "./CardImage.jsx";

function artLabel(printing) {
  if (!printing) return "unsorted";
  return printing.printing_variant ? printing.printing_variant : "base";
}

function placeLabel(holding) {
  return holding.kind === "deck" ? `${holding.container} (deck)` : `${holding.container} (${holding.kind})`;
}

// One card, opened over the grid: every art side by side with how many you own
// of each, where every copy is, and - the point of M1 (D-044) - recording which
// art some copies are, in the place they sit.
export default function CardDetail({ cardId, onClose, onChanged }) {
  const [card, setCard] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    setCard(null);
    setError(null);
    api.card(cardId).then(setCard).catch((e) => setError(e.message));
  }, [cardId]);

  useEffect(() => {
    const onKey = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const ownedOf = (imageId) =>
    card ? card.arts.filter((a) => a.card_image_id === imageId).reduce((n, a) => n + a.quantity, 0) : 0;

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
                             card={card} holding={h}
                             onSaved={(updated) => { setCard(updated); onChanged(); }} />
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

// One pile: N copies of one art in one place. "Set art" opens a picker: click
// the art they are, say how many, save. The total in the place never changes -
// only how it's split by art (D-030). The server refuses anything impossible
// and writes nothing; its message is shown here.
function Holding({ card, holding, onSaved }) {
  const [open, setOpen] = useState(false);
  const [target, setTarget] = useState(null);
  const [count, setCount] = useState(holding.quantity);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState(null);

  // After a save the pile is smaller (or gone): start the picker over.
  useEffect(() => {
    setCount(holding.quantity);
    setTarget(null);
    setOpen(false);
  }, [holding.quantity]);

  const current = card.printings.find((p) => p.card_image_id === holding.card_image_id);
  const choices = card.printings.filter((p) => p.card_image_id !== holding.card_image_id);

  async function save() {
    setSaving(true);
    setError(null);
    try {
      const result = await api.setArt({
        card: card.base_card_id,
        quantity: count,
        art: target,
        place: holding.container,
        kind: holding.kind,
        from_art: holding.card_image_id, // null = the unsorted ones
      });
      onSaved(result.card);
    } catch (e) {
      setError(e.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <li className="holding">
      <div className="holding-row">
        <span className="qty">{holding.quantity}×</span>
        <span className={holding.card_image_id ? "art-name" : "art-name todo"}>{artLabel(current)}</span>
        <span>in {placeLabel(holding)}</span>
        {choices.length > 0 && (
          <button className="small" onClick={() => setOpen(!open)} aria-expanded={open}>
            {open ? "Cancel" : holding.card_image_id ? "Change art" : "Set art"}
          </button>
        )}
      </div>
      {open && (
        <div className="picker">
          <p>Which art are they? Click it:</p>
          <div className="choices">
            {choices.map((p) => (
              <button key={p.card_image_id}
                      className={target === p.card_image_id ? "choice chosen" : "choice"}
                      onClick={() => setTarget(p.card_image_id)}
                      aria-pressed={target === p.card_image_id}>
                <CardImage src={p.image_url} alt={artLabel(p)} label={p.card_image_id} size="thumb" />
                <span>{artLabel(p)}</span>
              </button>
            ))}
          </div>
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
