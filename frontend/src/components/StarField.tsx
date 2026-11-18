import { useEffect, useRef } from "react";

const COLORS = [
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

export default function StarField() {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current!;
    const parent = canvas.parentElement!;
    const ctx = canvas.getContext("2d")!;
    let timer = 0;

    function paint() {
      const rect = parent.getBoundingClientRect();
      const w = Math.max(1, Math.round(rect.width));
      const h = Math.max(1, Math.round(rect.height));
      const dpr = Math.min(window.devicePixelRatio || 1, 2);

      canvas.style.width = w + "px";
      canvas.style.height = h + "px";
      canvas.width = Math.round(w * dpr);
      canvas.height = Math.round(h * dpr);
      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, w, h);
      const rand = rng(12345);

      // Samanyolu bandı
      const angle = -0.35;
      const bandH = h * 0.55;
      ctx.save();
      ctx.translate(w / 2, h / 2);
      ctx.rotate(angle);
      const g = ctx.createLinearGradient(0, -bandH / 2, 0, bandH / 2);
      g.addColorStop(0, "rgba(190,200,255,0)");
      g.addColorStop(0.5, "rgba(190,200,255,0.11)");
      g.addColorStop(1, "rgba(190,200,255,0)");
      ctx.fillStyle = g;
      ctx.fillRect(-w, -bandH / 2, w * 2, bandH);
      ctx.restore();

      function star(x: number, y: number, tinyBias: number) {
        const r = Math.min(0.999, rand() + tinyBias);
        let size: number, alpha: number, glow = false;
        if (r < 0.84) { size = 0.5 + rand() * 0.7; alpha = 0.25 + rand() * 0.45; }
        else if (r < 0.97) { size = 1.2 + rand() * 0.8; alpha = 0.55 + rand() * 0.35; }
        else { size = 2 + rand() * 1.2; alpha = 0.95; glow = true; }
        const c = COLORS[Math.floor(rand() * COLORS.length)];
        ctx.beginPath();
        ctx.arc(x, y, size, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(${c},${alpha})`;
        if (glow) { ctx.shadowColor = `rgba(${c},.8)`; ctx.shadowBlur = size * 5; }
        ctx.fill();
        ctx.shadowBlur = 0;
      }

      const count = Math.min(900, Math.round((w * h) / 2600));
      for (let i = 0; i < count; i++) star(rand() * w, rand() * h, 0);

      const cos = Math.cos(angle), sin = Math.sin(angle);
      for (let i = 0; i < count * 0.8; i++) {
        const u = (rand() - 0.5) * w * 1.6;
        const v = ((rand() + rand() + rand() - 1.5) / 1.5) * (bandH / 2.4);
        star(w / 2 + u * cos - v * sin, h / 2 + u * sin + v * cos, -0.12);
      }
    }

    paint();
    const onResize = () => {
      window.clearTimeout(timer);
      timer = window.setTimeout(paint, 200);
    };
    window.addEventListener("resize", onResize);
    return () => {
      window.clearTimeout(timer);
      window.removeEventListener("resize", onResize);
    };
  }, []);

  return (
    <canvas
      ref={ref}
      aria-hidden="true"
      style={{ position: "absolute", left: 0, top: 0, pointerEvents: "none" }}
    />
  );
}