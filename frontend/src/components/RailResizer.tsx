import { useEffect, useRef, useState } from "react";

const MIN = 240;
const MAX = 560;
const DEFAULT = 320;
const KEY = "ember-rail-width";

function clamp(n: number): number {
  const hi = Math.max(MIN, Math.min(MAX, Math.round(window.innerWidth * 0.5)));
  return Math.max(MIN, Math.min(hi, n));
}

function initial(): number {
  try {
    const v = Number(localStorage.getItem(KEY));
    if (v >= MIN && v <= MAX) return v;
  } catch {
    /* depolama kapalıysa varsayılan kalır */
  }
  return DEFAULT;
}

export default function RailResizer() {
  const ref = useRef<HTMLDivElement>(null);
  const [w, setW] = useState<number>(initial);
  const [drag, setDrag] = useState(false);

  // genişliği .layout üzerindeki --rail değişkenine yaz
  useEffect(() => {
    const layout = ref.current?.parentElement;
    if (layout) layout.style.setProperty("--rail", w + "px");
    try {
      localStorage.setItem(KEY, String(w));
    } catch {
      /* yoksay */
    }
  }, [w]);

  // sürüklerken metin seçilmesin, imleç sabit kalsın
  useEffect(() => {
    if (!drag) return;
    const prev = document.body.style.userSelect;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
    return () => {
      document.body.style.userSelect = prev;
      document.body.style.cursor = "";
    };
  }, [drag]);

  const onDown = (e: React.PointerEvent<HTMLDivElement>) => {
    e.currentTarget.setPointerCapture(e.pointerId);
    setDrag(true);
  };
  const onMove = (e: React.PointerEvent<HTMLDivElement>) => {
    if (!drag) return;
    const layout = ref.current?.parentElement;
    if (!layout) return;
    setW(clamp(e.clientX - layout.getBoundingClientRect().left));
  };
  const onUp = () => setDrag(false);
  const onKey = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === "ArrowLeft") { e.preventDefault(); setW((x) => clamp(x - 16)); }
    if (e.key === "ArrowRight") { e.preventDefault(); setW((x) => clamp(x + 16)); }
    if (e.key === "Home") { e.preventDefault(); setW(DEFAULT); }
  };

  return (
    <div
      ref={ref}
      className={"rail-resizer" + (drag ? " dragging" : "")}
      role="separator"
      aria-orientation="vertical"
      aria-label="Resize filter panel"
      aria-valuemin={MIN}
      aria-valuemax={MAX}
      aria-valuenow={w}
      tabIndex={0}
      title="Drag to resize, double-click to reset"
      onPointerDown={onDown}
      onPointerMove={onMove}
      onPointerUp={onUp}
      onPointerCancel={onUp}
      onDoubleClick={() => setW(DEFAULT)}
      onKeyDown={onKey}
    />
  );
}