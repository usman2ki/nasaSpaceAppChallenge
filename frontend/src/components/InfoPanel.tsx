import { useState } from "react";

export function InfoPanel() {
  const [open, setOpen] = useState(false);
  return (
    <section className="note small" aria-label="Why harmonisation">
      <button className="btn" onClick={() => setOpen(!open)} aria-expanded={open}>
        Why do we need harmonisation?
      </button>
      {open && (
        <p>
          MODIS and VIIRS have different spatial resolutions and detection
          characteristics. Ember Atlas applies statistical calibration to make
          their historical fire-activity observations more comparable. In
          simple terms: VIIRS sees more small fires, so its counts are scaled
          to match MODIS Aqua.
        </p>
      )}
    </section>
  );
}
