import { useState } from "react";

// A card picture, hotlinked from the card site (PHASE_2: no local image cache
// until Phase 5). About 60 cards in the catalog have no picture, and a picture
// can fail to load offline - both show a plain placeholder with the id instead.
export default function CardImage({ src, alt, label, size = "grid" }) {
  const [failed, setFailed] = useState(false);
  if (!src || failed) {
    return (
      <div className={`card-image placeholder ${size}`} role="img" aria-label={alt}>
        <span>{label || alt}</span>
      </div>
    );
  }
  return (
    <img
      className={`card-image ${size}`}
      src={src}
      alt={alt}
      loading="lazy"
      onError={() => setFailed(true)}
    />
  );
}
