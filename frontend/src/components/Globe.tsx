import { useEffect, useRef } from "react";
import { geoOrthographic, geoPath, geoGraticule10, geoDistance } from "d3-geo";
import { feature } from "topojson-client";
import worldData from "world-atlas/countries-110m.json";

type Props = { lon?: number; lat?: number };
const SIZE = 520;

export default function Globe({ lon = -47.25, lat = -10.25 }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = ref.current!;
    const ctx = canvas.getContext("2d")!;
    const topo: any = worldData;
    const land: any = feature(topo, topo.objects.countries);
    const graticule = geoGraticule10();
    const sphere: any = { type: "Sphere" };
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

    const dpr = Math.min(window.devicePixelRatio || 1, 1.5);
    canvas.width = SIZE * dpr;
    canvas.height = SIZE * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    const proj = geoOrthographic().translate([SIZE / 2, SIZE / 2]).clipAngle(90);
    const path = geoPath(proj, ctx);

    let rot: [number, number] = [-lon, -lat * 0.5];
    let zoom = 1;
    let dragging = false;
    let lastX = 0, lastY = 0;
    let idleSince = 0;
    let visible = true;
    let last = 0;
    let raf = 0;

    function draw(t: number) {
      const r = (SIZE / 2) * 0.60 * zoom;
      proj.scale(r).rotate([rot[0], rot[1], 0]);
      ctx.clearRect(0, 0, SIZE, SIZE);

      const glow = ctx.createRadialGradient(SIZE / 2, SIZE / 2, r * 0.95, SIZE / 2, SIZE / 2, Math.min(r * 1.18, SIZE / 2));
      glow.addColorStop(0, "rgba(232,114,28,.35)");
      glow.addColorStop(1, "rgba(232,114,28,0)");
      ctx.fillStyle = glow;
      ctx.beginPath(); ctx.arc(SIZE / 2, SIZE / 2, r * 1.18, 0, Math.PI * 2); ctx.fill();

      const ocean = ctx.createRadialGradient(SIZE * 0.4, SIZE * 0.35, r * 0.1, SIZE / 2, SIZE / 2, r);
      ocean.addColorStop(0, "#1f3040");
      ocean.addColorStop(1, "#0b121a");
      ctx.beginPath(); path(sphere); ctx.fillStyle = ocean; ctx.fill();

      ctx.beginPath(); path(graticule);
      ctx.strokeStyle = "rgba(255,255,255,.07)"; ctx.lineWidth = 0.6; ctx.stroke();

      ctx.beginPath(); path(land);
      ctx.fillStyle = "#2c3b49"; ctx.fill();
      ctx.strokeStyle = "rgba(232,114,28,.45)"; ctx.lineWidth = 0.6; ctx.stroke();

      const front = geoDistance([lon, lat], [-rot[0], -rot[1]]) < Math.PI / 2;
      const p = proj([lon, lat]);
      if (front && p) {
        const pulse = (t / 900) % 1;
        ctx.beginPath(); ctx.arc(p[0], p[1], 4 + pulse * 18, 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(255,154,46,${1 - pulse})`; ctx.lineWidth = 2; ctx.stroke();
        ctx.beginPath(); ctx.arc(p[0], p[1], 8, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(255,90,0,.35)"; ctx.fill();
        ctx.beginPath(); ctx.arc(p[0], p[1], 4.5, 0, Math.PI * 2);
        ctx.fillStyle = "#ffb347"; ctx.fill();
      }

      if (!reduce && !dragging && t - idleSince > 2000) rot[0] += 0.25;
    }

    function frame(t: number) {
      raf = requestAnimationFrame(frame);
      if (!visible || document.hidden) return;
      if (t - last < 33) return; // ~30 kare/sn
      last = t;
      draw(t);
    }
    raf = requestAnimationFrame(frame);

    const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting; });
    io.observe(canvas);

    const down = (e: PointerEvent) => { dragging = true; lastX = e.clientX; lastY = e.clientY; canvas.setPointerCapture(e.pointerId); };
    const move = (e: PointerEvent) => {
      if (!dragging) return;
      rot[0] += (e.clientX - lastX) * 0.4;
      rot[1] = Math.max(-80, Math.min(80, rot[1] - (e.clientY - lastY) * 0.4));
      lastX = e.clientX; lastY = e.clientY;
    };
    const up = () => { dragging = false; idleSince = performance.now(); };
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      zoom = Math.max(0.7, Math.min(2.2, zoom * (e.deltaY < 0 ? 1.08 : 0.92)));
    };
    canvas.addEventListener("pointerdown", down);
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerup", up);
    canvas.addEventListener("pointercancel", up);
    canvas.addEventListener("wheel", wheel, { passive: false });

    return () => {
      cancelAnimationFrame(raf);
      io.disconnect();
      canvas.removeEventListener("pointerdown", down);
      canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerup", up);
      canvas.removeEventListener("pointercancel", up);
      canvas.removeEventListener("wheel", wheel);
    };
  }, [lon, lat]);

  return (
    <canvas
      ref={ref}
      aria-label="Rotatable globe showing the pilot area"
      style={{ width: "100%", maxWidth: SIZE, height: "auto", aspectRatio: "1", display: "block", margin: "0 auto", cursor: "grab", touchAction: "none" }}
    />
  );
}