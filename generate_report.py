#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Informe diario de KEY LEVELS del futuro Nasdaq-100 (/NQ).
Baja datos de yfinance (NQ=F, barras 1m), separa RTH vs Globex, calcula el perfil
de volumen (POC / VAH / VAL) por franja, arma niveles clave, escenarios y trae
titulares de Finnhub. Genera el HTML del informe + las notas del release (mail).

Corre en GitHub Actions una vez por dia (~18:30 ART). No requiere histori­co largo:
usa solo la ultima sesion, por eso el limite de 7 dias de 1m de yfinance no molesta.
"""
import os, sys, json, datetime as dt
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import requests

NY   = ZoneInfo("America/New_York")
ART  = ZoneInfo("America/Argentina/Buenos_Aires")
TICKER   = "NQ=F"
BIN_SIZE = 2.0        # puntos por fila del perfil
VA_PCT   = 0.70       # value area 70%
NEWS_MAX = 6
FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "")

# ---------------------------------------------------------------- data
def load_session():
    """Devuelve (full, rth, on) DataFrames de la ultima sesion CME (18:00 prev -> 17:00 ET)."""
    import yfinance as yf
    df = yf.download(TICKER, period="3d", interval="1m", progress=False, auto_adjust=False)
    if df is None or len(df) == 0:
        raise RuntimeError("yfinance no devolvio datos para " + TICKER)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.title)[["Open", "High", "Low", "Close", "Volume"]].dropna()
    idx = df.index
    if idx.tz is None:
        idx = idx.tz_localize("UTC")
    df.index = idx.tz_convert(NY)

    # Fin de sesion = hoy 17:00 ET (o el ultimo timestamp si la rueda sigue abierta)
    last = df.index[-1]
    sess_date = last.date()
    end   = dt.datetime.combine(sess_date, dt.time(17, 0), tzinfo=NY)
    start = dt.datetime.combine(sess_date - dt.timedelta(days=1), dt.time(18, 0), tzinfo=NY)
    full = df[(df.index >= start) & (df.index <= end)].copy()
    if len(full) == 0:
        full = df.copy()
    rth_mask = [(t.date() == sess_date and dt.time(9, 30) <= t.time() < dt.time(16, 0)) for t in full.index]
    rth = full[rth_mask].copy()
    on  = full[[not m for m in rth_mask]].copy()
    return full, rth, on, sess_date

# ---------------------------------------------------------------- volume profile
def volume_profile(df, bin_size=BIN_SIZE, va_pct=VA_PCT):
    if len(df) == 0:
        return None
    lo = np.floor(df["Low"].min() / bin_size) * bin_size
    hi = np.ceil(df["High"].max() / bin_size) * bin_size
    centers = np.arange(lo, hi + bin_size, bin_size) + bin_size / 2
    centers = centers[:-1] if len(centers) > 1 else centers
    vol = np.zeros(len(centers))
    for l, h, c, v in zip(df["Low"], df["High"], df["Close"], df["Volume"]):
        idx = np.where((centers >= l - bin_size / 2) & (centers <= h + bin_size / 2))[0]
        if len(idx) == 0:
            vol[int(np.argmin(abs(centers - c)))] += v
        else:
            vol[idx] += v / len(idx)
    poc_i = int(np.argmax(vol))
    total = vol.sum()
    tgt = total * va_pct
    lo_i = hi_i = poc_i
    acc = vol[poc_i]
    while acc < tgt:
        up = vol[hi_i + 1] if hi_i + 1 < len(vol) else -1
        dn = vol[lo_i - 1] if lo_i - 1 >= 0 else -1
        if up == -1 and dn == -1:
            break
        if up >= dn:
            hi_i += 1; acc += vol[hi_i]
        else:
            lo_i -= 1; acc += vol[lo_i]
    # low volume nodes (single prints aprox): bins < 15% del promedio, fuera del value area
    avg = vol[vol > 0].mean() if (vol > 0).any() else 0
    lvn = [centers[i] for i in range(len(vol)) if 0 < vol[i] < 0.15 * avg]
    return dict(POC=float(centers[poc_i]), VAL=float(centers[lo_i]), VAH=float(centers[hi_i]),
                total=float(total), lvn=lvn)

def stats(df):
    if len(df) == 0:
        return None
    return dict(high=float(df["High"].max()), low=float(df["Low"].min()),
                open=float(df["Open"].iloc[0]), close=float(df["Close"].iloc[-1]),
                vol=float(df["Volume"].sum()))

# ---------------------------------------------------------------- news
def fetch_news():
    if not FINNHUB_KEY:
        return []
    try:
        r = requests.get("https://finnhub.io/api/v1/news",
                         params={"category": "general", "token": FINNHUB_KEY}, timeout=20)
        r.raise_for_status()
        data = r.json()
    except Exception as e:
        print("WARN news:", e, file=sys.stderr)
        return []
    kw = ("nasdaq", "stock", "fed", "inflation", "rate", "treasury", "yield",
          "tech", "wall street", "market", "s&p", "powell", "jobs", "cpi", "pce", "earnings")
    out = []
    for a in data:
        h = (a.get("headline") or "")
        if any(k in h.lower() for k in kw):
            t = dt.datetime.fromtimestamp(a.get("datetime", 0), tz=NY)
            out.append(dict(time=t.strftime("%H:%M"), headline=h,
                            source=a.get("source", ""), url=a.get("url", "")))
        if len(out) >= NEWS_MAX:
            break
    return out

# ---------------------------------------------------------------- helpers render
def fmt(x, d=2):
    return f"{x:,.{d}f}"

def pos_pct(price, hi, lo):
    if hi == lo:
        return 50.0
    return max(0.0, min(100.0, (hi - price) / (hi - lo) * 100.0))

# ---------------------------------------------------------------- HTML
CSS = open(os.path.join(os.path.dirname(__file__), "report.css")).read() \
      if os.path.exists(os.path.join(os.path.dirname(__file__), "report.css")) else ""

def render_html(ctx):
    from html import escape
    f, rth, on = ctx["full"], ctx["rth"], ctx["on"]
    vp = ctx["vp_full"]
    last = f["close"]; prev = ctx["prev_close"]
    chg = last - prev; chgp = (chg / prev * 100) if prev else 0
    cls = "red" if chg < 0 else "grn"
    arrow = "▼" if chg < 0 else "▲"
    hi, lo = f["high"], f["low"]

    # niveles ordenados de mayor a menor
    round_lvl = round(vp["POC"] / 100) * 100
    levels = [
        ("Maximo de sesion", hi, "res", "Resistencia mayor"),
        ("VAH · borde value", vp["VAH"], "res", "Resistencia"),
        (f"Redondo", round_lvl, "neu", "Psicologico"),
        ("POC", vp["POC"], "poc", "Pivote / iman"),
        ("VAL · borde value", vp["VAL"], "sup", "Soporte"),
        ("Minimo de sesion", lo, "sup", "Soporte mayor"),
    ]
    levels = sorted(levels, key=lambda x: -x[1])

    ladder = ""
    for name, px, kind, _ in levels:
        top = pos_pct(px, hi, lo)
        ladder += f'<div class="lv {kind}" style="top:{top:.1f}%"><span class="tag">{escape(name.split(" ")[0])}</span><span class="line"></span><span class="px mono">{fmt(px,0)}</span></div>'
    # last marker
    ladder += f'<div class="lv last" style="top:{pos_pct(last,hi,lo):.1f}%"><span class="tag">Cierre</span><span class="line"></span><span class="px mono">{fmt(last,0)}</span></div>'

    rows = ""
    for name, px, kind, tipo in levels:
        d = px - last
        dcls = "red" if d > 0 else ("grn" if d < 0 else "mut")
        pill = {"res":"p-res","sup":"p-sup","poc":"p-piv","neu":"p-neu"}[kind]
        rows += f'<tr><td>{escape(name)}</td><td class="mono">{fmt(px)}</td><td><span class="pill {pill}">{escape(tipo)}</span></td><td class="mono {dcls}" style="text-align:right">{d:+.0f}</td></tr>'

    def bk(title, sub, s, vp2, cls_, vpct):
        if s is None:
            return f'<div class="bk {cls_}"><div class="bt">{title}</div><div class="bh">{sub}</div><div class="r"><span>sin datos</span><span>-</span></div></div>'
        va = f'{fmt(vp2["VAL"],0)} – {fmt(vp2["VAH"],0)}' if vp2 else "-"
        poc = fmt(vp2["POC"], 0) if vp2 else "-"
        col = "var(--mut)" if cls_ == "on" else "var(--blu)"
        return (f'<div class="bk {cls_}"><div class="bt">{title}</div><div class="bh">{sub}</div>'
                f'<div class="r"><span>Max / Min</span><span class="mono">{fmt(s["high"],0)} / {fmt(s["low"],0)}</span></div>'
                f'<div class="r"><span>Rango</span><span class="mono">{fmt(s["high"]-s["low"],2)} pts</span></div>'
                f'<div class="r"><span>POC</span><span class="mono amb">{poc}</span></div>'
                f'<div class="r"><span>Value Area</span><span class="mono blu">{va}</span></div>'
                f'<div class="r"><span>Volumen</span><span class="mono">{vpct}</span></div>'
                f'<div class="volbar"><i style="width:{vpct};background:{col}"></i></div></div>')

    tot_v = f["vol"] or 1
    rthv = f'{(rth["vol"]/tot_v*100):.0f}%' if rth else "0%"
    onv  = f'{(on["vol"]/tot_v*100):.0f}%' if on else "0%"
    breakdown = (bk("Sesion completa", "Punta a punta · 18:00→17:00", f, vp, "full", "100%")
               + bk("RTH · rueda regular", "9:30 → 16:00 NY", rth, ctx["vp_rth"], "rth", rthv)
               + bk("Overnight · Globex", "18:00 → 9:30 · el resto", on, ctx["vp_on"], "on", onv))

    news = ctx["news"]
    if news:
        ni = "".join(f'<div class="ni"><span class="tm mono">{escape(n["time"])}</span>'
                     f'<span class="tx1"><a href="{escape(n["url"])}" target="_blank">{escape(n["headline"])}</a>'
                     f'<span class="src"> — {escape(n["source"])}</span></span></div>' for n in news)
    else:
        ni = '<div class="ni"><span class="tx1 mut">Sin titulares disponibles (revisar FINNHUB_KEY).</span></div>'

    # escenarios
    va_h, va_l, poc = vp["VAH"], vp["VAL"], vp["POC"]
    scen = (f'<div class="s up"><div class="t up">▲ Alcista</div>Aceptacion sostenida sobre <b class="mono">{fmt(poc,0)}</b> '
            f'→ objetivos <span class="mono">{fmt(va_h,0)}</span> (VAH) y maximo <span class="mono">{fmt(hi,0)}</span>.</div>'
            f'<div class="s dn"><div class="t dn">▼ Bajista</div>Perdida de <b class="mono">{fmt(va_l,0)}</b> (VAL) '
            f'→ reabre hacia el minimo <span class="mono">{fmt(lo,0)}</span>.</div>'
            f'<div class="s bl"><div class="t bl">◆ Balance</div>Entre <b class="mono">{fmt(va_l,0)}</b> y '
            f'<b class="mono">{fmt(va_h,0)}</b> → rango; operar los extremos.</div>')

    prov = ctx.get("provisional", False)
    badge = '<div class="badge">⚠ PROVISORIO</div>' if prov else '<div class="badge ok">✓ CIERRE</div>'
    fecha = ctx["date"].strftime("%A %d de %B de %Y")

    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NQ Key Levels — {ctx['date']}</title><style>{CSS}</style></head>
<body><div class="nqr">
  <div class="hd"><div><h1>NASDAQ-100 FUTURES · KEY LEVELS DIARIOS</h1>
  <div class="sub">/MNQ · /NQ — {fecha} · Perfil RTH + Globex</div></div>{badge}</div>
  <div class="headline"><span class="big mono">{fmt(last)}</span>
  <span class="chg {cls} mono">{arrow} {chg:+.2f} ({chgp:+.2f}%)</span>
  <span class="rng mono">Sesion completa · H {fmt(hi,0)} / L {fmt(lo,0)} · rango {fmt(hi-lo,2)} pts</span></div>
  <h3>Desglose por sesion</h3><div class="breakdown">{breakdown}</div>
  <h3>Noticias del dia</h3><div class="news">{ni}</div>
  <div class="grid2">
    <div><h3>Escalera de niveles</h3><div class="ladder">{ladder}</div></div>
    <div><h3>Tabla de niveles</h3><table><thead><tr><th>Nivel</th><th>Precio</th><th>Tipo</th><th style="text-align:right">Dist.</th></tr></thead><tbody>{rows}</tbody></table></div>
  </div>
  <h3>Escenarios para la proxima rueda</h3><div class="scen">{scen}</div>
  <div class="ft"><span>Generado automaticamente · {dt.datetime.now(ART).strftime('%d/%m/%Y %H:%M')} ART</span>
  <span>Fuente: yfinance (perfil aprox.) + Finnhub · no es recomendacion de inversion</span></div>
</div></body></html>"""

def render_release_md(ctx):
    f = ctx["full"]; vp = ctx["vp_full"]
    last = f["close"]; prev = ctx["prev_close"]
    chg = last - prev; chgp = (chg / prev * 100) if prev else 0
    d = ctx["date"].strftime("%Y-%m-%d")
    lines = [
        f"# Nasdaq-100 futures ({fmt(last,2)}, {chgp:+.2f}%) — key levels {d}",
        "",
        f"Sesion del {d}",
        "",
        f"- Cierre: {fmt(last)} ({chg:+.2f}, {chgp:+.2f}%)",
        f"- Rango: H {fmt(f['high'],0)} / L {fmt(f['low'],0)} ({fmt(f['high']-f['low'],2)} pts)",
        f"- POC: {fmt(vp['POC'],2)}  ·  Value Area: {fmt(vp['VAL'],0)}–{fmt(vp['VAH'],0)}",
        f"- Resistencias: {fmt(vp['VAH'],0)} (VAH), {fmt(f['high'],0)} (max)",
        f"- Soportes: {fmt(vp['VAL'],0)} (VAL), {fmt(f['low'],0)} (min)",
        "",
        f"Informe completo: https://{ctx['gh_user']}.github.io/{ctx['gh_repo']}/informes/{d}.html",
    ]
    return "\n".join(lines)

# ---------------------------------------------------------------- main
def main():
    full_df, rth_df, on_df, sess_date = load_session()
    ctx = dict(
        full=stats(full_df), rth=stats(rth_df), on=stats(on_df),
        vp_full=volume_profile(full_df), vp_rth=volume_profile(rth_df), vp_on=volume_profile(on_df),
        news=fetch_news(),
        date=sess_date,
        prev_close=float(full_df["Open"].iloc[0]),  # aprox: se puede mejorar con cierre previo real
        provisional=(dt.datetime.now(NY).time() < dt.time(16, 5) and dt.datetime.now(NY).date() == sess_date),
        gh_user=os.environ.get("GH_USER", "alphainvestment"),
        gh_repo=os.environ.get("GH_REPO", "Nasdaq"),
    )
    d = sess_date.strftime("%Y-%m-%d")
    os.makedirs("informes", exist_ok=True)
    html = render_html(ctx)
    with open(f"informes/{d}.html", "w", encoding="utf-8") as fp:
        fp.write(html)
    with open("index.html", "w", encoding="utf-8") as fp:
        fp.write(html)  # index = ultimo informe
    with open("RELEASE_NOTES.md", "w", encoding="utf-8") as fp:
        fp.write(render_release_md(ctx))
    print(f"OK informe {d}: cierre {fmt(ctx['full']['close'])} POC {fmt(ctx['vp_full']['POC'])}")

if __name__ == "__main__":
    main()
