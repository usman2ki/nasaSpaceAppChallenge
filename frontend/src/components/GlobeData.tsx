import { useEffect, useMemo, useRef } from "react";
import { geoOrthographic, geoPath, geoGraticule10, geoDistance } from "d3-geo";
import { feature } from "topojson-client";
import worldData from "world-atlas/countries-110m.json";

type Props = {
  aoi?: any | null;
  cells?: any | null;
  platforms?: string[];
  colorBy?: string;
};

const SIZE = 520;
const BASE = 0.7; // küre yarıçapı = yarı boyut * BASE * zoom (ışıma için pay bırakır)
const DEFAULT: [number, number] = [-47.25, -10.25];
const EMPTY: string[] = [];

function cellValue(p: any, colorBy: string, platforms: string[]): number {
  if (colorBy === "__pooled__") return platforms.reduce((s, pl) => s + (Number(p["q_" + pl]) || 0), 0);
  return Number(p["q_" + colorBy]) || 0;
}

function ringsOf(geom: any): any[] {
  if (!geom) return [];
  if (geom.type === "Polygon") return [geom.coordinates[0]];
  if (geom.type === "MultiPolygon") return geom.coordinates.map((p: any) => p[0]);
  return [];
}

// Merkez ve boyutu doğrudan koordinatlardan hesapla (poligon yönünden etkilenmez)
function extentOf(geom: any): { center: [number, number]; ext: number } | null {
  const rings = ringsOf(geom);
  let minLon = Infinity, maxLon = -Infinity, minLat = Infinity, maxLat = -Infinity;
  let count = 0;
  for (const r of rings) {
    for (const c of r) {
      if (c[0] < minLon) minLon = c[0];
      if (c[0] > maxLon) maxLon = c[0];
      if (c[1] < minLat) minLat = c[1];
      if (c[1] > maxLat) maxLat = c[1];
      count++;
    }
  }
  if (!count) return null;
  let cLon = (minLon + maxLon) / 2;
  let span = maxLon - minLon;
  if (span > 180) {
    // 180. boylamı geçen alan
    let a = Infinity, b = -Infinity;
    for (const r of rings) {
      for (const c of r) {
        const l = c[0] < 0 ? c[0] + 360 : c[0];
        if (l < a) a = l;
        if (l > b) b = l;
      }
    }
    cLon = (a + b) / 2;
    if (cLon > 180) cLon -= 360;
    span = b - a;
  }
  return { center: [cLon, (minLat + maxLat) / 2], ext: Math.max(span, maxLat - minLat, 0.5) };
}

export default function GlobeData({ aoi = null, cells = null, platforms = EMPTY, colorBy = "__pooled__" }: Props) {
  const ref = useRef<HTMLCanvasElement>(null);

  // Veriden türetilen her şey (bölge merkezi, sınır çizgileri, hücre noktaları)
  const derived = useMemo(() => {
    let center: [number, number] = DEFAULT;
    let ext = 40;
    const lines = ringsOf(aoi).map((r) => ({ type: "LineString", coordinates: r }));
    const e = extentOf(aoi);
    if (e) {
      center = e.center;
      ext = e.ext;
    }
    const feats: any[] = cells && cells.features ? cells.features : [];
    const step = Math.max(1, Math.ceil(feats.length / 4000));
    const pts: { lon: number; lat: number; v: number }[] = [];
    let max = 0;
    for (let i = 0; i < feats.length; i += step) {
      const f = feats[i];
      const ring = f.geometry && f.geometry.type === "Polygon" ? f.geometry.coordinates[0] : null;
      if (!ring || ring.length < 4) continue;
      const n = ring.length - 1;
      let lo = 0, la = 0;
      for (let k = 0; k < n; k++) { lo += ring[k][0]; la += ring[k][1]; }
      const v = cellValue(f.properties || {}, colorBy, platforms);
      if (v <= 0) continue;
      if (v > max) max = v;
      pts.push({ lon: lo / n, lat: la / n, v });
    }
    return { center, ext, lines, pts, max };
  }, [aoi, cells, platforms, colorBy]);

  const dataRef = useRef(derived);
  dataRef.current = derived;

  const st = useRef({
    rot: [-DEFAULT[0], -DEFAULT[1] * 0.5] as [number, number],
    zoom: 1,
    targetZoom: 1.15,
    userZoom: false,
    idleSince: 0,
  });

  // Bölge değişince küre oraya dönsün; küçük bölgelerde çok hafif yaklaşsın
  useEffect(() => {
    const s = st.current;
    s.targetZoom = derived.ext < 20 ? 1.15 : 1;
    s.userZoom = false;
    s.idleSince = -1e9;
  }, [derived.center[0], derived.center[1], derived.ext]);

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

    const s = st.current;
    let dragging = false;
    let lastX = 0, lastY = 0;
    let visible = true;
    let last = 0;
    let raf = 0;

    function draw(t: number) {
      const d = dataRef.current;

      // seçili bölgeye süzülerek dön (sürükledikten 4 sn sonra)
      if (!dragging && t - s.idleSince > 4000) {
        const sway = reduce ? 0 : Math.sin(t / 2500) * 4;
        const tl = -d.center[0] + sway;
        const tp = Math.max(-80, Math.min(80, -d.center[1]));
        const dl = ((((tl - s.rot[0] + 180) % 360) + 360) % 360) - 180;
        s.rot[0] += dl * 0.06;
        s.rot[1] += (tp - s.rot[1]) * 0.06;
      }
      if (!s.userZoom) s.zoom += (s.targetZoom - s.zoom) * 0.05;

      const r = (SIZE / 2) * BASE * s.zoom;
      proj.scale(r).rotate([s.rot[0], s.rot[1], 0]);
      ctx.clearRect(0, 0, SIZE, SIZE);

      // küre çizim alanından büyükse yuvarlak mercek gibi göster
      const lens = r > SIZE / 2 - 2;
      ctx.save();
      if (lens) {
        ctx.beginPath();
        ctx.arc(SIZE / 2, SIZE / 2, SIZE / 2 - 1, 0, Math.PI * 2);
        ctx.clip();
      } else {
        // ışıma: dairesel ve çizim alanının içinde biter, kenarlarda kesilmez
        const outer = Math.min(r * 1.22, SIZE / 2 - 1);
        const glow = ctx.createRadialGradient(SIZE / 2, SIZE / 2, r * 0.96, SIZE / 2, SIZE / 2, outer);
        glow.addColorStop(0, "rgba(232,114,28,.38)");
        glow.addColorStop(0.5, "rgba(232,114,28,.12)");
        glow.addColorStop(1, "rgba(232,114,28,0)");
        ctx.fillStyle = glow;
        ctx.beginPath(); ctx.arc(SIZE / 2, SIZE / 2, outer, 0, Math.PI * 2); ctx.fill();
      }

      const ocean = ctx.createRadialGradient(SIZE * 0.4, SIZE * 0.35, r * 0.1, SIZE / 2, SIZE / 2, r);
      ocean.addColorStop(0, "#1f3040");
      ocean.addColorStop(1, "#0b121a");
      ctx.beginPath(); path(sphere); ctx.fillStyle = ocean; ctx.fill();

      ctx.beginPath(); path(graticule);
      ctx.strokeStyle = "rgba(255,255,255,.07)"; ctx.lineWidth = 0.6; ctx.stroke();

      ctx.beginPath(); path(land);
      ctx.fillStyle = "#2c3b49"; ctx.fill();
      ctx.strokeStyle = "rgba(232,114,28,.35)"; ctx.lineWidth = 0.6; ctx.stroke();

      const view: [number, number] = [-s.rot[0], -s.rot[1]];
      const limit = Math.PI / 2 - 0.02;
      const front = geoDistance(d.center, view) < limit;
      const m = proj(d.center);

      // bölge küre üzerinde ne kadar yer kaplıyor (piksel)?
      const boxPx = d.ext * ((r * Math.PI) / 180);
      const small = boxPx < 44;

      if (small) {
        // küçük bölge: kare yerine yumuşak parlayan nokta
        if (front && m) {
          const gr = Math.max(10, boxPx * 0.8);
          const hasData = d.pts.length > 0;
          const g = ctx.createRadialGradient(m[0], m[1], 0, m[0], m[1], gr);
          g.addColorStop(0, hasData ? "rgba(255,190,90,.95)" : "rgba(255,190,90,.6)");
          g.addColorStop(0.45, hasData ? "rgba(255,130,40,.5)" : "rgba(255,130,40,.3)");
          g.addColorStop(1, "rgba(232,114,28,0)");
          ctx.fillStyle = g;
          ctx.beginPath(); ctx.arc(m[0], m[1], gr, 0, Math.PI * 2); ctx.fill();
        }
      } else {
        // büyük bölge: hücre noktaları ve kesik çizgili sınır
        const logMax = Math.log1p(d.max) || 1;
        const dotR = Math.max(0.9, Math.min(3, s.zoom * 0.7));
        for (const c of d.pts) {
          if (geoDistance([c.lon, c.lat], view) > limit) continue;
          const p = proj([c.lon, c.lat]);
          if (!p) continue;
          const tn = Math.log1p(c.v) / logMax;
          ctx.beginPath();
          ctx.arc(p[0], p[1], dotR * (0.6 + tn * 0.7), 0, Math.PI * 2);
          ctx.fillStyle = `rgba(255,${Math.round(190 - 130 * tn)},${Math.round(90 - 70 * tn)},${(0.35 + 0.45 * tn).toFixed(2)})`;
          ctx.fill();
        }
        if (d.lines.length) {
          ctx.save();
          ctx.setLineDash([5, 4]);
          ctx.beginPath();
          for (const ln of d.lines) path(ln as any);
          ctx.strokeStyle = "rgba(255,179,71,.9)"; ctx.lineWidth = 1.2; ctx.stroke();
          ctx.restore();
        }
      }

      // nabız atan halka
      if (front && m) {
        const pulse = (t / 1100) % 1;
        ctx.beginPath(); ctx.arc(m[0], m[1], 5 + pulse * 20, 0, Math.PI * 2);
        ctx.strokeStyle = `rgba(255,154,46,${(1 - pulse) * 0.8})`; ctx.lineWidth = 1.6; ctx.stroke();
        if (small) {
          ctx.beginPath(); ctx.arc(m[0], m[1], 2.2, 0, Math.PI * 2);
          ctx.fillStyle = "#fff3d6"; ctx.fill();
        }
      }
      ctx.restore();

      // mercek çerçevesi
      if (lens) {
        ctx.beginPath();
        ctx.arc(SIZE / 2, SIZE / 2, SIZE / 2 - 2, 0, Math.PI * 2);
        ctx.strokeStyle = "rgba(232,114,28,.55)"; ctx.lineWidth = 2; ctx.stroke();
      }
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
      s.rot[0] += (e.clientX - lastX) * 0.4 / Math.max(1, s.zoom * 0.6);
      s.rot[1] = Math.max(-80, Math.min(80, s.rot[1] - (e.clientY - lastY) * 0.4 / Math.max(1, s.zoom * 0.6)));
      lastX = e.clientX; lastY = e.clientY;
    };
    const up = () => { dragging = false; s.idleSince = performance.now(); };
    const wheel = (e: WheelEvent) => {
      e.preventDefault();
      s.userZoom = true;
      s.zoom = Math.max(0.7, Math.min(5, s.zoom * (e.deltaY < 0 ? 1.08 : 0.92)));
      s.idleSince = performance.now();
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
  }, []);

  return (
    <canvas
      ref={ref}
      aria-label="Rotatable globe showing the selected area and its detections"
      style={{ width: "100%", maxWidth: SIZE, height: "auto", aspectRatio: "1", display: "block", margin: "0 auto", cursor: "grab", touchAction: "none" }}
    />
  );
}