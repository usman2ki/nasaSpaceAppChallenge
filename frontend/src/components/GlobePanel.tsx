import { useMemo, useState } from "react";
import GlobeData from "./GlobeData";

const W = 2200;
const H = 1000;
const TINTS = [
  "255,255,255", "255,255,255", "255,255,255",
  "207,224,255", "169,198,255", "255,233,196", "255,210,161", "255,179,138",
];

function rng(seed: number) {
  return function () {
    seed |= 0; seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

type Opts = { minA: number; maxA: number; blur: number; spread: number };

function build(count: number, seed: number, o: Opts): string {
  const rand = rng(seed);
  const angle = -0.35;
  const cos = Math.cos(angle);
  const sin = Math.sin(angle);
  const bandH = H * 0.55;
  const parts: string[] = [];
  for (let i = 0; i < count; i++) {
    let x: number, y: number;
    if (i % 2 === 0) {
      // Samanyolu bandının içinde yoğunlaşan yıldızlar
      const u = (rand() - 0.5) * W * 1.5;
      const v = ((rand() + rand() + rand() - 1.5) / 1.5) * (bandH / 2.4);
      x = W / 2 + u * cos - v * sin;
      y = H / 2 + u * sin + v * cos;
    } else {
      x = rand() * W;
      y = rand() * H;
    }
    if (x < 0 || x > W || y < 0 || y > H) continue;
    const c = TINTS[Math.floor(rand() * TINTS.length)];
    const a = o.minA + rand() * (o.maxA - o.minA);
    parts.push(`${x.toFixed(0)}px ${y.toFixed(0)}px ${o.blur}px ${o.spread}px rgba(${c},${a.toFixed(2)})`);
  }
  return parts.join(",");
}

export default function GlobePanel(props: { aoi?: any; cells?: any; platforms?: string[]; colorBy?: string }) {
  const [open, setOpen] = useState(true);

  const layers = useMemo(
    () => ({
      far: build(620, 11, { minA: 0.18, maxA: 0.6, blur: 0, spread: 0 }),
      mid: build(200, 22, { minA: 0.45, maxA: 0.9, blur: 0, spread: 0 }),
      near: build(30, 33, { minA: 0.85, maxA: 1, blur: 4, spread: 1 }),
    }),
    []
  );

  return (
    <section className={"space-hero" + (open ? "" : " closed")} aria-label="Globe">
      <div className="stars" aria-hidden="true">
        <div className="mw" />
        <div className="star-layer far" style={{ boxShadow: layers.far }} />
        <div className="star-layer mid" style={{ boxShadow: layers.mid }} />
        <div className="star-layer near" style={{ boxShadow: layers.near }} />
      </div>

      <button className="btn space-toggle" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
        {open ? "Hide globe" : "Show globe"}
      </button>

      {open && (
        <div className="space-globe">
          <GlobeData {...props} />
          <p className="space-caption">
            Selected area and its detections (orange dots, log scale). Drag to rotate, scroll to zoom.
          </p>
        </div>
      )}
    </section>
  );
}