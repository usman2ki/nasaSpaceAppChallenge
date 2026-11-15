#!/usr/bin/env python3
"""
Ember Risk Alerts  -  recurrence-based fire alerts from MODIS + VIIRS (2024-2025)

Uses the same logic as the larp project, step by step:
  1. HARMONISE   (birlestir.py)          merge MODIS+VIIRS, de-duplicate into fire EVENTS
                                         (1.5 km / 30 min), keep one primary row per event
  2. SOURCE FILTER (kaynak_analizi.py)   keep vegetation fires only (type = 0), drop industry
  3. CALIBRATE   (kalibrasyon_hesapla.py) k_sensor, monthly N_MODIS = beta*N_VIIRS + gamma,
                                         FRP alpha (FRP_MODIS = alpha * sum FRP_VIIRS) so both
                                         sensors are expressed on the MODIS scale
New here:
  4. GRID + SEASON   0.1 deg cells (~10 km) x month; "did this cell burn in this season before?"
  5. BACKTEST        learn from one year, test on the other. The probability shown on every
                     alert is the MEASURED hit rate of that alert class, not an invented number.
  6. ALERTS          fire_alerts.html (map + list + per-cell history) and fire_risk_cells.csv

Usage (from the folder that holds your FIRMS CSV files):
  python fire_risk_alerts.py --modis MODIS_2024_2025.csv --viirs VIIRS_2024_2025.csv
  python fire_risk_alerts.py --modis a.csv b.csv --viirs c.csv --years 2024 2025 --cell 0.1
Needs: pandas numpy scipy   (pip install pandas numpy scipy)
"""
import argparse, glob, json, math, sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.sparse import csr_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
CLASS_NAMES = ["none", "watch", "moderate", "high", "critical"]   # codes 0..4


# --------------------------------------------------------------------------- 1. HARMONISE
def read_firms(paths, instrument):
    files = []
    for p in paths:
        files += glob.glob(p) or [p]
    frames = []
    for f in files:
        d = pd.read_csv(f, on_bad_lines="skip", low_memory=False)
        d = d.rename(columns={"bright_ti4": "brightness", "bright_ti5": "bright_t31"})
        d["instrument"] = instrument
        frames.append(d)
        print(f"  read {Path(f).name}: {len(d):,} rows")
    if not frames:
        sys.exit(f"No {instrument} file found.")
    return pd.concat(frames, ignore_index=True, sort=False)


def assign_events(df, spatial_km=1.5, temporal_min=30.0):
    """Identical idea to larp/birlestir.py: link detections closer than 1.5 km and 30 min."""
    df = df.copy().reset_index(drop=True)
    lat = np.radians(df["latitude"].to_numpy())
    x = df["longitude"].to_numpy() * 111.32 * np.cos(lat) / spatial_km
    y = df["latitude"].to_numpy() * 111.32 / spatial_km
    t = (df["acq_datetime"] - df["acq_datetime"].min()).dt.total_seconds().to_numpy() / 60.0
    ts = t / temporal_min
    tree = cKDTree(np.column_stack([x, y, ts]))
    pairs = tree.query_pairs(r=math.sqrt(2.0), output_type="ndarray")
    if len(pairs):
        i, j = pairs[:, 0], pairs[:, 1]
        dist = np.hypot((x[i] - x[j]) * spatial_km, (y[i] - y[j]) * spatial_km)
        keep = (dist <= spatial_km) & (np.abs(t[i] - t[j]) <= temporal_min)
        pairs = pairs[keep]
    n = len(df)
    if len(pairs):
        r = np.concatenate([pairs[:, 0], pairs[:, 1]])
        c = np.concatenate([pairs[:, 1], pairs[:, 0]])
        adj = csr_matrix((np.ones(len(r), dtype=np.int8), (r, c)), shape=(n, n))
        _, lab = connected_components(adj, directed=False)
        df["event_id"] = lab + 1
    else:
        df["event_id"] = np.arange(1, n + 1)
    df["_v"] = (df["instrument"] == "VIIRS").astype(int)
    rk = df.sort_values(["event_id", "_v", "confidence_norm", "frp", "acq_datetime"],
                        ascending=[True, False, False, False, True], na_position="last")
    primary = set(rk.drop_duplicates("event_id", keep="first").index)
    df["is_primary"] = df.index.isin(primary)
    df["event_sensors"] = df.groupby("event_id")["instrument"].transform(
        lambda s: "+".join(sorted(s.dropna().unique())))
    return df.drop(columns="_v")


def harmonise(modis, viirs):
    modis["confidence_raw"] = modis["confidence"].astype(str).str.strip()
    mnum = pd.to_numeric(modis["confidence_raw"], errors="coerce")
    modis["confidence_level"] = pd.cut(mnum, bins=[-1, 29, 79, 100],
                                       labels=["low", "nominal", "high"]).astype(str)
    modis["confidence_norm"] = (mnum / 100.0).round(3)

    viirs["confidence_raw"] = viirs["confidence"].astype(str).str.strip()
    cl = viirs["confidence_raw"].str.lower()
    viirs["confidence_level"] = cl.map({"l": "low", "low": "low", "n": "nominal", "nominal": "nominal",
                                        "h": "high", "high": "high"}).fillna("unknown")
    viirs["confidence_norm"] = viirs["confidence_level"].map({"low": .15, "nominal": .55, "high": .90})

    df = pd.concat([modis, viirs], ignore_index=True, sort=False)
    for c in ["latitude", "longitude", "scan", "track", "frp"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.dropna(subset=["latitude", "longitude"])
    d = pd.to_datetime(df["acq_date"], errors="coerce")
    tn = pd.to_numeric(df["acq_time"].astype(str).str.replace(":", "", regex=False).str.strip(), errors="coerce")
    ok = d.notna() & tn.notna()
    df = df.loc[ok].copy()
    tp = tn.loc[ok].astype(int).astype(str).str.zfill(4)
    df["acq_datetime"] = pd.to_datetime(d.loc[ok].dt.strftime("%Y-%m-%d") + " " + tp,
                                        format="%Y-%m-%d %H%M", utc=True, errors="coerce")
    df = df.dropna(subset=["acq_datetime"])
    df["pixel_area_km2"] = (df["scan"] * df["track"]).round(4)
    return assign_events(df)


# --------------------------------------------------------------------------- 3. CALIBRATE
def calibrate(df, tekil):
    """larp/kalibrasyon_hesapla.py: k_sensor, monthly regression, FRP alpha."""
    tekil = tekil.copy()
    tekil["ay"] = tekil["acq_datetime"].dt.month
    m = tekil[tekil["event_sensors"].str.contains("MODIS", na=False)]
    v = tekil[tekil["event_sensors"].str.contains("VIIRS", na=False)]
    k = len(v) / len(m) if len(m) else float("nan")
    am = m.groupby("ay").size().reindex(range(1, 13), fill_value=0).values
    av = v.groupby("ay").size().reindex(range(1, 13), fill_value=0).values
    beta, gamma = np.polyfit(av, am, 1)
    pred = beta * av + gamma
    ss_tot = np.sum((am - am.mean()) ** 2)
    r2 = 1 - np.sum((am - pred) ** 2) / ss_tot if ss_tot > 0 else float("nan")

    ids = tekil.loc[tekil["event_sensors"] == "MODIS+VIIRS", "event_id"].unique()
    both = df[df["event_id"].isin(ids)]
    s = both.groupby(["event_id", "instrument"])["frp"].sum().unstack()
    s = s.dropna()
    alpha = float(np.sum(s["VIIRS"] * s["MODIS"]) / np.sum(s["VIIRS"] ** 2)) if len(s) else float("nan")
    if not np.isfinite(alpha) or alpha <= 0:
        alpha = 1.0
        print("  WARNING: too few joint MODIS+VIIRS events, FRP alpha set to 1.0")
    cal = dict(k_sensor=round(k, 3), beta=round(float(beta), 4), gamma=round(float(gamma), 2),
               r2=round(float(r2), 3), alpha_frp=round(alpha, 4), n_joint_events=int(len(s)),
               n_modis_events=int(len(m)), n_viirs_events=int(len(v)))
    return cal


# --------------------------------------------------------------------------- 4/5. GRID, SEASON, BACKTEST
def wilson(k, n, z=1.645):
    if n == 0:
        return (float("nan"),) * 3
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0, c - h), min(1, c + h)


def build_table(ev, cell):
    """One row per (cell, year, month) with fire stats."""
    ev = ev.copy()
    ev["ci"] = np.floor(ev["latitude"] / cell).astype(int)
    ev["cj"] = np.floor(ev["longitude"] / cell).astype(int)
    ev["year"] = ev["acq_datetime"].dt.year
    ev["month"] = ev["acq_datetime"].dt.month
    ev["day"] = ev["acq_datetime"].dt.day
    ev["sens"] = ev["event_sensors"].map({"MODIS": 0, "VIIRS": 1, "MODIS+VIIRS": 2}).fillna(1).astype(int)
    g = ev.groupby(["ci", "cj", "year", "month"]).agg(
        n=("event_id", "size"), frp_sum=("frp_eq", "sum"), frp_max=("frp_eq", "max"),
        d1=("day", "min"), d2=("day", "max"), s=("sens", "max")).reset_index()
    return g


def classes_for(hist_years, tab, cells, big_n):
    """Class code per (cell, target month) from history years only.
    0 none | 1 watch (neighbour burned in season) | 2 moderate (own cell burned in season)
    3 high (own cell, >= big_n events in season) | 4 critical (own cell burned in >=2 history years)"""
    h = tab[tab["year"].isin(hist_years)]
    own = {}                                   # (ci,cj,m) -> {year: n}
    for r in h.itertuples(index=False):
        own.setdefault((r.ci, r.cj, r.month), {})[r.year] = r.n
    # season window = month-1, month, month+1
    win = {}
    for (ci, cj, m), yrs in own.items():
        for dm in (-1, 0, 1):
            tm = (m - 1 + dm) % 12 + 1
            slot = win.setdefault((ci, cj, tm), {})
            for y, n in yrs.items():
                slot[y] = slot.get(y, 0) + n
    out = {}
    for (ci, cj, tm), yrs in win.items():
        ny = len(yrs)
        mx = max(yrs.values())
        out[(ci, cj, tm)] = 4 if ny >= 2 else (3 if mx >= big_n else 2)
    # neighbours
    for (ci, cj, tm) in list(win.keys()):
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                if di == 0 and dj == 0:
                    continue
                k = (ci + di, cj + dj, tm)
                if k not in out:
                    out[k] = 1
    return out


def backtest(tab, years, cells, big_n):
    """Learn from year A, test on year B (every ordered pair). Outcome = any fire in that cell in that month."""
    outcome = {(r.ci, r.cj, r.year, r.month) for r in tab.itertuples(index=False)}
    rows = []
    folds = []
    for a in years:
        for b in years:
            if a == b:
                continue
            cls = classes_for([a], tab, cells, big_n)
            hit = {k: ((k[0], k[1], b, k[2]) in outcome) for k in cls}
            # universe for "none": every active-region cell x 12 months
            for (ci, cj) in cells:
                for m in range(1, 13):
                    c = cls.get((ci, cj, m), 0)
                    rows.append((a, b, c, (ci, cj, b, m) in outcome))
            folds.append((a, b))
    bt = pd.DataFrame(rows, columns=["train", "test", "cls", "hit"])
    res = {}
    for c in range(0, 4):                       # class 4 cannot be validated with one training year
        sub = bt[bt["cls"] == c]
        p, lo, hi = wilson(int(sub["hit"].sum()), len(sub))
        res[c] = dict(n=int(len(sub)), hits=int(sub["hit"].sum()), p=p, lo=lo, hi=hi)
    base = wilson(int(bt["hit"].sum()), len(bt))
    alerted = bt[bt["cls"] >= 2]
    tot_hits = bt["hit"].sum()
    recall = alerted["hit"].sum() / tot_hits if tot_hits else float("nan")
    summary = dict(folds=folds, base_rate=base[0], base_n=int(len(bt)),
                   share_of_cells_alerted=float(len(alerted) / len(bt)),
                   recall_of_moderate_or_higher=float(recall))
    return res, summary


# --------------------------------------------------------------------------- 6. OUTPUT
def zone_name(lat, lon):
    if lat >= 39.8 and lon < 31: return "Marmara & NW"
    if lat >= 40.3 and lon >= 31: return "Black Sea"
    if lon < 30 and lat < 39.8: return "Aegean"
    if lat < 37.6 and 30 <= lon < 37.5: return "Mediterranean"
    if lon >= 37.5 and lat < 38.5: return "Southeast"
    if lon >= 37.5: return "East"
    return "Central Anatolia"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--modis", nargs="+", required=True)
    ap.add_argument("--viirs", nargs="+", required=True)
    ap.add_argument("--years", nargs="+", type=int, default=[2024, 2025])
    ap.add_argument("--cell", type=float, default=0.1, help="grid size in degrees (0.1 = ~10 km)")
    ap.add_argument("--big", type=int, default=10, help="events in a season window that make a cell 'high'")
    ap.add_argument("--min-conf", default="nominal", choices=["low", "nominal", "high"])
    ap.add_argument("--today", default=str(date.today()))
    ap.add_argument("--out", default=".")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    print("1) Reading + harmonising MODIS and VIIRS (larp / birlestir logic)")
    modis = read_firms(a.modis, "MODIS")
    viirs = read_firms(a.viirs, "VIIRS")
    df = harmonise(modis, viirs)
    df = df[df["acq_datetime"].dt.year.isin(a.years)]
    if df.empty:
        sys.exit(f"No detections in years {a.years}.")
    tekil = df[df["is_primary"]].copy()
    print(f"   raw detections {len(df):,} -> unique fire events {len(tekil):,}")

    print("2) Keeping vegetation fires only (larp / kaynak_analizi logic)")
    tcode = pd.to_numeric(tekil.get("type"), errors="coerce")
    if tcode.notna().mean() < 0.5:
        print("   WARNING: 'type' column missing/empty (typical of NRT files); source filter skipped.")
        veg = tekil
    else:
        veg = tekil[tcode.fillna(-1).astype(int) == 0]
    rank = {"low": 0, "nominal": 1, "high": 2}
    veg = veg[veg["confidence_level"].map(rank).fillna(-1) >= rank[a.min_conf]].copy()
    print(f"   {len(veg):,} vegetation-fire events at confidence >= {a.min_conf}")

    print("3) Calibrating MODIS <-> VIIRS (larp / kalibrasyon logic)")
    cal = calibrate(df, tekil)
    print("  ", json.dumps(cal))
    veg["frp_eq"] = np.where(veg["instrument"] == "VIIRS", veg["frp"] * cal["alpha_frp"], veg["frp"])
    veg["frp_eq"] = veg["frp_eq"].fillna(0)

    print("4) Grid + season table")
    tab = build_table(veg, a.cell)
    years = sorted(int(y) for y in tab["year"].unique())
    print(f"   years with data: {years}; cells that burned: {tab[['ci','cj']].drop_duplicates().shape[0]:,}")
    if len(years) < 2:
        sys.exit("Need at least 2 years of data for a backtest.")
    act = set(zip(tab["ci"], tab["cj"]))
    cells = set()                                  # active region = burned cells + 1-cell ring
    for ci, cj in act:
        for di in (-1, 0, 1):
            for dj in (-1, 0, 1):
                cells.add((ci + di, cj + dj))

    print("5) Backtest (learn on one year, test on the other)")
    bt, summ = backtest(tab, years, cells, a.big)
    for c in range(4):
        r = bt[c]
        print(f"   class {CLASS_NAMES[c]:9s}: {r['hits']:>6,}/{r['n']:>7,} cells had fire = "
              f"{100*r['p']:.2f}% (90% CI {100*r['lo']:.2f}-{100*r['hi']:.2f}%)")
    print(f"   base rate for any cell-month: {100*summ['base_rate']:.2f}%; "
          f"alerts (moderate+) cover {100*summ['share_of_cells_alerted']:.1f}% of cell-months and catch "
          f"{100*summ['recall_of_moderate_or_higher']:.0f}% of the fires that happened")

    print("6) Writing alerts")
    cls = classes_for(years, tab, cells, a.big)
    hist = {}
    for r in tab.itertuples(index=False):
        hist.setdefault((r.ci, r.cj), []).append(
            [int(r.year), int(r.month), int(r.n), round(float(r.frp_max), 1), round(float(r.frp_sum), 1),
             int(r.d1), int(r.d2), int(r.s)])
    cells_out, csv_rows = [], []
    for (ci, cj) in sorted({(k[0], k[1]) for k in cls}):
        codes = "".join(str(cls.get((ci, cj, m), 0)) for m in range(1, 13))
        if codes == "0" * 12:
            continue
        lat0, lon0 = ci * a.cell, cj * a.cell
        cells_out.append([round(lat0, 4), round(lon0, 4), codes, sorted(hist.get((ci, cj), []))])
        for m in range(1, 13):
            c = int(codes[m - 1])
            if c:
                pr = bt.get(min(c, 3))
                csv_rows.append(dict(
                    lat_south=round(lat0, 4), lon_west=round(lon0, 4), cell_deg=a.cell,
                    area=zone_name(lat0 + a.cell / 2, lon0 + a.cell / 2), month=MONTHS[m - 1],
                    alert=CLASS_NAMES[c], est_fire_probability=round(pr["p"], 4),
                    ci90_low=round(pr["lo"], 4), ci90_high=round(pr["hi"], 4),
                    probability_note=("measured on 1 history year; recurrent cells are at least this high" if c == 4
                                      else "measured by backtest")))
    pd.DataFrame(csv_rows).to_csv(out / "fire_risk_cells.csv", index=False)

    payload = dict(cell=a.cell, years=[int(y) for y in years], today=a.today, cells=cells_out,
                   probs={str(c): dict(p=bt[min(c, 3)]["p"], lo=bt[min(c, 3)]["lo"], hi=bt[min(c, 3)]["hi"],
                                       n=bt[min(c, 3)]["n"]) for c in range(1, 5)},
                   base=summ["base_rate"], cal=cal, big=a.big, conf=a.min_conf,
                   recall=summ["recall_of_moderate_or_higher"], share=summ["share_of_cells_alerted"],
                   nev=int(len(veg)))
    html = HTML.replace("__DATA__", json.dumps(payload, separators=(",", ":"), default=float))
    (out / "fire_alerts.html").write_text(html, encoding="utf-8")
    (out / "backtest.json").write_text(json.dumps(
        dict(calibration=cal, backtest={CLASS_NAMES[c]: bt[c] for c in range(4)},
             summary=summ, years=[int(y) for y in years]), indent=2, default=float), encoding="utf-8")
    print(f"\nDone: {out/'fire_alerts.html'} | {out/'fire_risk_cells.csv'} | {out/'backtest.json'}")
    print("Open fire_alerts.html in a browser (internet needed only for the map background, Esri; choose 'None' to work fully offline).")


HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Ember Risk Alerts</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css">
<style>
:root{--bg:#f6f7f4;--fg:#1b1f1a;--card:#fff;--mut:#566052;--bd:#d0d5cb;
--c1:#6aa9d8;--c2:#e8c20c;--c3:#e8730c;--c4:#c62828}
@media(prefers-color-scheme:dark){:root{--bg:#12150f;--fg:#eef1ea;--card:#1b2018;--mut:#a3ab9f;--bd:#333a2f}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,Segoe UI,Arial,sans-serif}
header{padding:10px 14px;border-bottom:1px solid var(--bd)}h1{font-size:1.25rem;margin:0}
.n{color:var(--mut);font-size:.85rem;margin:.3em 0 0}
.bar{display:flex;flex-wrap:wrap;gap:10px;padding:10px 14px;align-items:end}
label{display:flex;flex-direction:column;font-weight:600;font-size:.8rem;gap:3px}
select,button{font:inherit;padding:6px 8px;border-radius:8px;border:1px solid var(--bd);background:var(--card);color:var(--fg)}
.wrap{display:grid;grid-template-columns:minmax(0,1.5fr) minmax(300px,1fr);gap:12px;padding:0 14px 14px}
@media(max-width:850px){.wrap{grid-template-columns:1fr}}
#map{height:560px;border-radius:10px;border:1px solid var(--bd)}
.panel{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:10px;max-height:560px;overflow:auto}
.item{padding:7px;border-bottom:1px solid var(--bd);cursor:pointer}.item:hover{background:rgba(120,120,120,.12)}
.tag{display:inline-block;padding:1px 8px;border-radius:99px;color:#fff;font-weight:700;font-size:.75rem}
.t1{background:var(--c1)}.t2{background:#b8960a}.t3{background:var(--c3)}.t4{background:var(--c4)}
.detail{margin:0 14px 14px;background:var(--card);border:1px solid var(--bd);border-left:6px solid var(--c3);border-radius:10px;padding:12px}
.kp{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;padding:0 14px 10px}
.k{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:8px}.k b{font-size:1.3rem;display:block}
small,.m{color:var(--mut)}table{border-collapse:collapse;width:100%;margin-top:6px}td,th{text-align:left;padding:4px 8px;border-bottom:1px solid var(--bd);font-size:.85rem}
</style></head><body>
<header><h1>🔥 Ember Risk Alerts <small class="m">Türkiye &middot; learned from <span id="yrs"></span> MODIS + VIIRS vegetation fires</small></h1>
<p class="n"><b>How to read this:</b> an area is alerted when it burned in the same season in a previous year (or a neighbouring 10 km cell did). The percentage shown is the <u>measured</u> share of such areas that burned again in the backtest (learn on one year, test on the other). It is a recurrence-based estimate, not a weather-based fire forecast, and with two years of history it is rough.</p></header>
<div class="bar">
<label>Season (month)<select id="M"></select></label>
<label>Minimum alert<select id="L"><option value="2">Moderate and up</option><option value="3">High and up</option><option value="4">Critical only</option><option value="1">Everything (incl. Watch)</option></select></label>
<label>Area<select id="Z"></select></label>
<label>Map background<select id="BM"><option value="street">Street</option><option value="sat">Satellite</option><option value="none">None (offline)</option></select></label>
<button id="NOW">Go to current month</button></div>
<div class="kp" id="KP"></div>
<div class="wrap"><div id="map"></div><div class="panel" id="LS"></div></div>
<div class="detail" id="D"><b>Click a cell on the map or an item in the list</b> to see where and when it burned before and how likely it is to burn again.</div>
<div class="detail" style="border-left-color:var(--c1)"><b>Method (scientist view)</b><p class="m" id="SM"></p></div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script>
const D=__DATA__,$=i=>document.getElementById(i);
const MN=['January','February','March','April','May','June','July','August','September','October','November','December'];
const LN=['','Watch','Moderate','High','Critical'];
const COL=['','#6aa9d8','#e8c20c','#e8730c','#c62828'];
const zone=(la,lo)=>la>=39.8&&lo<31?'Marmara & NW':la>=40.3&&lo>=31?'Black Sea':lo<30&&la<39.8?'Aegean':la<37.6&&lo>=30&&lo<37.5?'Mediterranean':lo>=37.5&&la<38.5?'Southeast':lo>=37.5?'East':'Central Anatolia';
const ZS=['All areas','Marmara & NW','Aegean','Mediterranean','Black Sea','Central Anatolia','Southeast','East'];
ZS.forEach(z=>$('Z').add(new Option(z,z)));MN.forEach((m,i)=>$('M').add(new Option(m,i+1)));
$('yrs').textContent=D.years.join(' & ');
const pct=x=>(100*x).toFixed(x<.1?1:0)+'%';
const SEN=['MODIS','VIIRS','MODIS+VIIRS'];
const tm=+D.today.slice(5,7);$('M').value=tm;
let map=null,layer=null,sel=null;
const TUR=[[[41.335,36.913],[40.949,38.348],[41.103,39.513],[41.014,40.373],[41.536,41.554],[41.583,42.62],[41.092,43.583],[40.74,43.753],[40.254,43.656],[40.005,44.4],[39.713,44.794],[39.428,44.109],[38.281,44.421],[37.972,44.226],[37.17,44.773],[37.002,44.293],[37.256,43.942],[37.385,42.779],[37.23,42.35],[37.074,41.212],[37.091,40.673],[36.716,39.523],[36.713,38.7],[36.901,38.168],[36.623,37.067],[36.818,36.739],[36.26,36.685],[36.041,36.418],[35.822,36.15],[36.275,35.782],[36.651,36.161],[36.565,35.551],[36.796,34.715],[36.22,34.027],[36.108,32.509],[36.644,31.7],[36.678,30.622],[36.263,30.391],[36.144,29.7],[36.677,28.733],[36.659,27.641],[37.653,27.049],[38.208,26.318],[38.986,26.805],[39.464,26.171],[40.42,27.28],[40.46,28.82],[41.22,29.24],[41.088,31.146],[41.736,32.348],[42.019,33.513],[42.04,35.168],[41.335,36.913]],[[40.691,27.192],[40.152,26.358],[40.618,26.043],[40.824,26.057],[40.936,26.295],[41.562,26.604],[41.827,26.117],[42.141,27.136],[42.007,27.997],[41.623,28.116],[41.3,28.988],[41.055,28.806],[41.0,27.619],[40.691,27.192]]];
const BASES={street:['https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}','Tiles © Esri'],
 sat:['https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}','Tiles © Esri'],none:null};
let base=null;
function setBase(k){if(!map)return;if(base){map.removeLayer(base);base=null}
 const b=BASES[k];$('map').style.background=k==='none'?'#dfe5dc':'#ccc';if(!b)return;
 let err=0;base=L.tileLayer(b[0],{maxZoom:12,attribution:b[1]});
 base.on('tileerror',()=>{if(++err===4){map.removeLayer(base);base=null;$('BM').value='none';$('map').style.background='#dfe5dc'}});
 base.addTo(map);base.bringToBack()}
if(window.L){map=L.map('map').setView([39.0,35.2],6);
 L.polygon(TUR,{color:'#333',weight:1.5,fill:false,dashArray:'4 3',interactive:false}).addTo(map);
 layer=L.layerGroup().addTo(map);setBase('street')}
else $('map').innerHTML='<p class="m" style="padding:14px">Map library not loaded (offline?). The alert list still works.</p>';
function cellsFor(m){const mn=+$('M').value,min=+$('L').value,z=$('Z').value,o=[];
 D.cells.forEach((c,i)=>{const k=+c[2][mn-1];if(k>=min&&(z==='All areas'||zone(c[0]+D.cell/2,c[1]+D.cell/2)===z))o.push({i,k,c})});
 o.sort((a,b)=>b.k-a.k||histN(b.c,mn)-histN(a.c,mn));return o}
function histN(c,mn){return c[3].filter(h=>Math.abs(((h[1]-mn+18)%12)-6)<=1).reduce((s,h)=>s+h[2],0)}
function describe(c,mn){const k=+c[2][mn-1],P=D.probs[k],mon=MN[mn-1];
 const near=c[3].filter(h=>Math.abs(((h[1]-mn+18)%12)-6)<=1);
 let t=`<h3 style="margin:.2em 0"><span class="tag t${k}">${LN[k].toUpperCase()}</span> ${c[0].toFixed(2)}°N, ${c[1].toFixed(2)}°E &middot; ${zone(c[0]+D.cell/2,c[1]+D.cell/2)}</h3>`;
 if(near.length){t+=`<p><b>Fire was detected here before, around this time of year:</b></p><table><tr><th>When</th><th>Fire events</th><th>Days</th><th>Peak power (MODIS-equiv.)</th><th>Seen by</th></tr>`+
  near.map(h=>`<tr><td>${MN[h[1]-1]} ${h[0]}</td><td>${h[2]}</td><td>${h[5]}–${h[6]}</td><td>${h[3]} MW</td><td>${SEN[h[7]]}</td></tr>`).join('')+`</table>`;
 }else t+=`<p>No fire was detected in this exact cell in ${D.years.join('/')} in this season, but <b>neighbouring 10 km cells did burn</b> around this time of year.</p>`;
 t+=`<p><b>Chance of fire again in this area in ${mon}: ${k===4?'at least ':''}${pct(P.p)}</b> <small>(90% interval ${pct(P.lo)}–${pct(P.hi)}; a random cell in the active region: ${pct(D.base)})</small><br><small>`+
  (k===4?`Burned in the same season in both ${D.years.join(' and ')}: recurrent. Backtests with one year of history measured ${pct(D.probs[2].p)} (moderate) to ${pct(D.probs[3].p)} (high) for single-year cases; recurrence is at least that, but cannot be separately validated with only two years.`
  :`Measured in the backtest: of ${P.n.toLocaleString()} cell-seasons with this same alert, ${pct(P.p)} burned the following/previous year in that month.`)+`</small></p>`;
 return t}
function show(o){const c=o.c,mn=+$('M').value;$('D').innerHTML=describe(c,mn);$('D').style.borderLeftColor=COL[o.k];
 if(map){map.fitBounds([[c[0],c[1]],[c[0]+D.cell,c[1]+D.cell]],{maxZoom:10});if(sel)sel.setStyle({weight:1});}
 $('D').scrollIntoView({behavior:'smooth',block:'nearest'})}
function R(){const mn=+$('M').value,list=cellsFor(),cnt=[0,0,0,0,0];
 D.cells.forEach(c=>cnt[+c[2][mn-1]]++);
 $('KP').innerHTML=`<div class="k"><b>${list.length.toLocaleString()}</b>cells alerted for ${MN[mn-1]}</div>`+
 [4,3,2,1].map(k=>`<div class="k"><b style="color:${COL[k]}">${cnt[k].toLocaleString()}</b>${LN[k]} <small>(${k===4?'both years':pct(D.probs[k].p)+' hit rate'})</small></div>`).join('');
 if(layer){layer.clearLayers();list.forEach(o=>{const r=L.rectangle([[o.c[0],o.c[1]],[o.c[0]+D.cell,o.c[1]+D.cell]],{color:COL[o.k],weight:1,fillOpacity:.55});
  r.on('click',()=>{sel=r;show(o)});r.bindTooltip(`${LN[o.k]} · ${MN[mn-1]}`);layer.addLayer(r)})}
 $('LS').innerHTML=list.slice(0,200).map((o,j)=>`<div class="item" data-j="${j}"><span class="tag t${o.k}">${LN[o.k]}</span> ${o.c[0].toFixed(2)}°N ${o.c[1].toFixed(2)}°E <small>${zone(o.c[0]+D.cell/2,o.c[1]+D.cell/2)}</small><br><small>${o.c[3].length?histN(o.c,mn)+' fire events in this season before':'neighbouring cells burned in this season'}</small></div>`).join('')||'<p class="m">No alerts for this selection.</p>';
 document.querySelectorAll('.item').forEach(e=>e.onclick=()=>show(list[+e.dataset.j]));
 if(list.length>200)$('LS').insertAdjacentHTML('beforeend',`<p class="m">Showing the top 200 of ${list.length.toLocaleString()}; the map shows all.</p>`)}
['M','L','Z'].forEach(i=>$(i).onchange=R);$('BM').onchange=()=>setBase($('BM').value);$('NOW').onclick=()=>{$('M').value=tm;R()};
$('SM').innerHTML=`Pipeline = larp logic on ${D.years.join('–')} data: MODIS+VIIRS detections are merged into unique events (1.5 km / 30 min), restricted to vegetation fires (type 0, confidence ≥ ${D.conf}), and calibrated: VIIRS saw k = ${D.cal.k_sensor}× the MODIS events; monthly N_MODIS = ${D.cal.beta}·N_VIIRS + (${D.cal.gamma}), R² = ${D.cal.r2}; FRP_MODIS = ${D.cal.alpha_frp}·ΣFRP_VIIRS (${D.cal.n_joint_events.toLocaleString()} joint events). ${D.nev.toLocaleString()} events are placed on a ${D.cell}° grid. Alert classes: <i>Watch</i> neighbouring cell burned in ±1 month; <i>Moderate</i> own cell burned in ±1 month; <i>High</i> ≥ ${D.big} events in that window; <i>Critical</i> burned in that window in ≥ 2 years. Probabilities come from a backtest: classes built from one year, scored on whether the cell had a fire in the target month of the other year (Wilson 90% interval). Moderate-or-higher alerts cover ${pct(D.share)} of cell-seasons and catch ${pct(D.recall)} of the fires that occurred. Limits: two years is a short record; recurrence ≠ weather/fuel/ignition forecast; arson, agriculture and industry-type noise that FIRMS labels as vegetation fire can still appear.`;
R();
</script></body></html>
"""

if __name__ == "__main__":
    main()