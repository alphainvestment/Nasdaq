#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Informe diario de KEY LEVELS del futuro Nasdaq-100 (/NQ).
Datos: yfinance (NQ=F 1m) para el perfil de volumen, FMP para el calendario
economico y Finnhub para los titulares. Genera el HTML + las notas del mail.
Corre en GitHub Actions ~18:30 ART. Usa solo la ultima sesion.
"""
import os, sys, datetime as dt
from zoneinfo import ZoneInfo
import numpy as np
import pandas as pd
import requests
from components import COMPONENTS, SKIP_PRICE

NY   = ZoneInfo("America/New_York")
ART  = ZoneInfo("America/Argentina/Buenos_Aires")
TICKER   = "NQ=F"
BIN_SIZE = 2.0
VA_PCT   = 0.70
NEWS_MAX = 6
FINNHUB_KEY = os.environ.get("FINNHUB_KEY", "")
FMP_KEY     = os.environ.get("FMP_KEY", "")

# ---------------------------------------------------------------- data
def load_session():
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
    poc_i = int(np.argmax(vol)); total = vol.sum(); tgt = total * va_pct
    lo_i = hi_i = poc_i; acc = vol[poc_i]
    while acc < tgt:
        up = vol[hi_i + 1] if hi_i + 1 < len(vol) else -1
        dn = vol[lo_i - 1] if lo_i - 1 >= 0 else -1
        if up == -1 and dn == -1: break
        if up >= dn: hi_i += 1; acc += vol[hi_i]
        else: lo_i -= 1; acc += vol[lo_i]
    return dict(POC=float(centers[poc_i]), VAL=float(centers[lo_i]), VAH=float(centers[hi_i]), total=float(total))

def stats(df):
    if len(df) == 0: return None
    return dict(high=float(df["High"].max()), low=float(df["Low"].min()),
                open=float(df["Open"].iloc[0]), close=float(df["Close"].iloc[-1]), vol=float(df["Volume"].sum()))

# ---------------------------------------------------------------- relato (sin fuente externa)
def session_narrative(full, rth, on, s, vp):
    if full is None or len(full) < 5:
        return "Datos insuficientes para el relato de la sesion."
    hhmm = lambda ts: ts.strftime("%H:%M")
    t_hi = full["High"].idxmax(); t_lo = full["Low"].idxmin(); t_vol = full["Volume"].idxmax()
    op, cl, hi, lo, poc = s["open"], s["close"], s["high"], s["low"], vp["POC"]
    frag = []
    if on is not None and len(on):
        frag.append(f"El overnight se movio entre {fmt(on['Low'].min(),0)} y {fmt(on['High'].max(),0)} con volumen liviano")
    if rth is not None and len(rth):
        frag.append(f"y la rueda regular abrio cerca de {fmt(float(rth['Open'].iloc[0]),0)}")
    p1 = (", ".join(frag) + ".") if frag else ""
    if t_lo < t_hi:
        seq = (f"El minimo del dia ({fmt(lo,0)}) se marco a las {hhmm(t_lo)} y desde ahi el precio se recupero "
               f"hasta el maximo de {fmt(hi,0)} a las {hhmm(t_hi)}.")
    else:
        seq = (f"El maximo del dia ({fmt(hi,0)}) quedo a las {hhmm(t_hi)} y luego el precio cedio "
               f"hasta el minimo de {fmt(lo,0)} a las {hhmm(t_lo)}.")
    climax = f"El pico de volumen se dio a las {hhmm(t_vol)}, el momento de mayor actividad."
    rel = "por encima" if cl > poc else ("por debajo" if cl < poc else "sobre")
    tone = "cierre firme" if cl >= op else "cierre debil"
    close_s = f"Termino en {fmt(cl,0)} ({cl-op:+.0f} pts vs apertura), {rel} del POC ({fmt(poc,0)}) — {tone}."
    return " ".join(x for x in [p1, seq, climax, close_s] if x).strip()

# ---------------------------------------------------------------- calendario (FMP)
def fetch_calendar(day):
    if not FMP_KEY: return None
    ds = day.strftime("%Y-%m-%d")
    try:
        r = requests.get("https://financialmodelingprep.com/stable/economic-calendar",
                         params={"from": ds, "to": ds, "apikey": FMP_KEY}, timeout=20)
        r.raise_for_status(); data = r.json()
    except Exception as e:
        print("WARN cal:", e, file=sys.stderr); return None
    out = []
    for ev in data if isinstance(data, list) else []:
        if (ev.get("country") or "").upper() not in ("US", "USA", "UNITED STATES"): continue
        out.append(dict(time=(ev.get("date") or "")[11:16], event=ev.get("event", ""),
                        actual=ev.get("actual"), estimate=ev.get("estimate"),
                        previous=ev.get("previous"), impact=(ev.get("impact") or "")))
    out.sort(key=lambda x: x["time"])
    return out

# ---------------------------------------------------------------- titulares (Finnhub)
def fetch_news():
    if not FINNHUB_KEY: return None
    try:
        r = requests.get("https://finnhub.io/api/v1/news", params={"category": "general", "token": FINNHUB_KEY}, timeout=20)
        r.raise_for_status(); data = r.json()
    except Exception as e:
        print("WARN news:", e, file=sys.stderr); return None
    kw = ("nasdaq","stock","fed","inflation","rate","treasury","yield","tech","wall street",
          "market","s&p","powell","jobs","cpi","pce","earnings","gdp")
    out = []
    for a in data:
        h = a.get("headline") or ""
        if any(k in h.lower() for k in kw):
            t = dt.datetime.fromtimestamp(a.get("datetime", 0), tz=NY)
            out.append(dict(time=t.strftime("%H:%M"), headline=h, source=a.get("source", ""), url=a.get("url", "")))
        if len(out) >= NEWS_MAX: break
    return out


# ---------------------------------------------------------------- performance por sector (componentes)
def fetch_component_moves():
    import yfinance as yf
    tickers=[t for (t,_,_,_) in COMPONENTS if t not in SKIP_PRICE]
    try:
        raw=yf.download(tickers, period="5d", interval="1d", progress=False, auto_adjust=False)
    except Exception as e:
        print("WARN moves:", e, file=sys.stderr); return {}
    try:
        lv=raw.columns.get_level_values(0)
        px=raw["Close"] if "Close" in lv else raw
    except Exception:
        px=raw
    px=px.dropna(how="all")
    if len(px)<2: return {}
    last=px.iloc[-1]; prev=px.iloc[-2]; out={}
    for t in tickers:
        try:
            p=prev[t]; l=last[t]
            if p==p and l==l and p: out[t]=float(l)/float(p)-1
        except Exception: pass
    return out

def agg_moves(moves):
    from collections import defaultdict
    sw=defaultdict(float); swr=defaultdict(float); iw=defaultdict(float); iwr=defaultdict(float)
    for t,wt,sec,ind in COMPONENTS:
        if t in moves:
            r=moves[t]; sw[sec]+=wt; swr[sec]+=wt*r; iw[(sec,ind)]+=wt; iwr[(sec,ind)]+=wt*r
    sectors={s:(swr[s]/sw[s], sw[s]) for s in sw if sw[s]>0}
    niches={k:(iwr[k]/iw[k], iw[k]) for k in iw if iw[k]>0}
    return sectors, niches

# ---------------------------------------------------------------- helpers
def fmt(x, d=2):
    try: return f"{float(x):,.{d}f}"
    except Exception: return "-"

def num(x):
    return "-" if x in (None, "") else str(x)

def pos_pct(price, hi, lo):
    return 50.0 if hi == lo else max(0.0, min(100.0, (hi - price) / (hi - lo) * 100.0))

CSS = open(os.path.join(os.path.dirname(__file__), "report.css")).read() if os.path.exists(os.path.join(os.path.dirname(__file__), "report.css")) else ""

EXTRA_CSS = """
.relato{background:var(--card2);border-left:3px solid var(--blu);border-radius:8px;padding:13px 16px;font-size:13.5px;margin-bottom:6px}
.cal{width:100%;border-collapse:collapse;font-size:12.5px}
.cal th{text-align:left;color:var(--mut);font-size:10px;text-transform:uppercase;letter-spacing:.5px;padding:6px 8px;border-bottom:1px solid var(--bd)}
.cal td{padding:6px 8px;border-bottom:1px solid rgba(48,54,61,.6)}
.cal .beat{color:var(--grn);font-weight:700}.cal .miss{color:var(--red);font-weight:700}
.hi3{color:var(--amb)}
.sect{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:8px 14px;margin-bottom:12px}
.secrow{display:flex;align-items:center;gap:10px;padding:5px 0;font-size:12.5px;border-top:1px solid rgba(48,54,61,.5)}
.secrow:first-child{border-top:none}
.secrow .sn{width:180px}.secrow .sbar{flex:1;height:14px;position:relative;background:rgba(139,148,158,.08);border-radius:3px}
.secrow .sbar .mid{position:absolute;left:50%;top:-2px;bottom:-2px;width:1px;background:var(--bd)}
.secrow .sbar i{position:absolute;top:2px;bottom:2px;border-radius:2px}
.secrow .sbar i.up{left:50%;background:var(--grn)}.secrow .sbar i.dn{right:50%;background:var(--red)}
.secrow .sv{width:64px;text-align:right;font-weight:700}.secrow .sw{width:52px;text-align:right;font-size:11px}
.niches{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:12px}
.ncol{background:var(--card);border:1px solid var(--bd);border-radius:10px;padding:10px 14px}
.nh{font-size:11px;font-weight:700;margin-bottom:8px}
.compsep{text-align:center;color:var(--mut);font-size:11px;letter-spacing:1.5px;text-transform:uppercase;margin:26px 0 2px;border-top:1px solid var(--bd);padding-top:16px}
.nrow{display:flex;align-items:center;gap:8px;font-size:12px;padding:3px 0}
.nrow .nn{flex:1}.nrow .nv{width:60px;text-align:right;font-weight:700}.nrow .nw{width:46px;text-align:right;font-size:10.5px}
@media(max-width:720px){.niches{grid-template-columns:1fr}.secrow .sn{width:120px}}
"""

def render_html(ctx):
    from html import escape
    f, rth, on = ctx["full"], ctx["rth"], ctx["on"]
    vp = ctx["vp_full"]
    last = f["close"]; prev = ctx["prev_close"]
    chg = last - prev; chgp = (chg / prev * 100) if prev else 0
    cls = "red" if chg < 0 else "grn"; arrow = "▼" if chg < 0 else "▲"
    hi, lo = f["high"], f["low"]

    round_lvl = round(vp["POC"] / 100) * 100
    levels = sorted([
        ("Maximo de sesion", hi, "res", "Resistencia mayor"),
        ("VAH · borde value", vp["VAH"], "res", "Resistencia"),
        ("Redondo", round_lvl, "neu", "Psicologico"),
        ("POC", vp["POC"], "poc", "Pivote / iman"),
        ("VAL · borde value", vp["VAL"], "sup", "Soporte"),
        ("Minimo de sesion", lo, "sup", "Soporte mayor"),
    ], key=lambda x: -x[1])

    ladder = ""
    for name, px, kind, _ in levels:
        ladder += f'<div class="lv {kind}" style="top:{pos_pct(px,hi,lo):.1f}%"><span class="tag">{escape(name.split(" ")[0])}</span><span class="line"></span><span class="px mono">{fmt(px,0)}</span></div>'
    ladder += f'<div class="lv last" style="top:{pos_pct(last,hi,lo):.1f}%"><span class="tag">Cierre</span><span class="line"></span><span class="px mono">{fmt(last,0)}</span></div>'

    rows = ""
    for name, px, kind, tipo in levels:
        d = px - last; dcls = "red" if d > 0 else ("grn" if d < 0 else "mut")
        pill = {"res":"p-res","sup":"p-sup","poc":"p-piv","neu":"p-neu"}[kind]
        rows += f'<tr><td>{escape(name)}</td><td class="mono">{fmt(px)}</td><td><span class="pill {pill}">{escape(tipo)}</span></td><td class="mono {dcls}" style="text-align:right">{d:+.0f}</td></tr>'

    def bk(title, s, vp2, cls_, vpct):
        if s is None:
            return f'<div class="bk {cls_}"><div class="bt">{title}</div><div class="r"><span>sin datos</span><span>-</span></div></div>'
        va = f'{fmt(vp2["VAL"],0)} – {fmt(vp2["VAH"],0)}' if vp2 else "-"; poc = fmt(vp2["POC"], 0) if vp2 else "-"
        col = "var(--mut)" if cls_ == "on" else "var(--blu)"
        return (f'<div class="bk {cls_}"><div class="bt">{title}</div>'
                f'<div class="r"><span>Max / Min</span><span class="mono">{fmt(s["high"],0)} / {fmt(s["low"],0)}</span></div>'
                f'<div class="r"><span>Rango</span><span class="mono">{fmt(s["high"]-s["low"],2)} pts</span></div>'
                f'<div class="r"><span>POC</span><span class="mono amb">{poc}</span></div>'
                f'<div class="r"><span>Value Area</span><span class="mono blu">{va}</span></div>'
                f'<div class="r"><span>Volumen</span><span class="mono">{vpct}</span></div>'
                f'<div class="volbar"><i style="width:{vpct};background:{col}"></i></div></div>')
    tot_v = f["vol"] or 1
    rthv = f'{(rth["vol"]/tot_v*100):.0f}%' if rth else "0%"; onv = f'{(on["vol"]/tot_v*100):.0f}%' if on else "0%"
    breakdown = (bk("Sesion completa", f, vp, "full", "100%") + bk("RTH · 9:30-16:00 NY", rth, ctx["vp_rth"], "rth", rthv)
               + bk("Overnight · Globex", on, ctx["vp_on"], "on", onv))

    # calendario
    cal = ctx["calendar"]
    if cal is None:
        cal_html = '<div class="news"><div class="ni"><span class="tx1 mut">Calendario no disponible (falta FMP_KEY).</span></div></div>'
    elif len(cal) == 0:
        cal_html = '<div class="news"><div class="ni"><span class="tx1 mut">Sin eventos economicos de EE.UU. hoy.</span></div></div>'
    else:
        crows = ""
        for e in cal:
            a, est = e["actual"], e["estimate"]; scls = ""
            try:
                if a not in (None,"") and est not in (None,""):
                    scls = "beat" if float(a) > float(est) else ("miss" if float(a) < float(est) else "")
            except Exception: scls = ""
            imp = '<span class="hi3">★★★</span>' if str(e["impact"]).lower() in ("high","3") else ""
            crows += (f'<tr><td class="mono mut">{escape(e["time"])}</td><td>{escape(e["event"])} {imp}</td>'
                      f'<td class="mono {scls}">{escape(num(a))}</td><td class="mono mut">{escape(num(est))}</td>'
                      f'<td class="mono mut">{escape(num(e["previous"]))}</td></tr>')
        cal_html = (f'<table class="cal"><thead><tr><th>Hora</th><th>Evento</th><th>Actual</th><th>Esper.</th><th>Previo</th></tr></thead>'
                    f'<tbody>{crows}</tbody></table>')

    # titulares
    news = ctx["news"]
    if news is None:
        ni = '<div class="ni"><span class="tx1 mut">Titulares no disponibles (falta FINNHUB_KEY).</span></div>'
    elif len(news) == 0:
        ni = '<div class="ni"><span class="tx1 mut">Sin titulares relevantes hoy.</span></div>'
    else:
        ni = "".join(f'<div class="ni"><span class="tm mono">{escape(n["time"])}</span>'
                     f'<span class="tx1"><a href="{escape(n["url"])}" target="_blank">{escape(n["headline"])}</a>'
                     f'<span class="src"> — {escape(n["source"])}</span></span></div>' for n in news)

    va_h, va_l, poc = vp["VAH"], vp["VAL"], vp["POC"]
    scen = (f'<div class="s up"><div class="t up">▲ Alcista</div>Aceptacion sostenida sobre <b class="mono">{fmt(poc,0)}</b> '
            f'→ objetivos <span class="mono">{fmt(va_h,0)}</span> (VAH) y maximo <span class="mono">{fmt(hi,0)}</span>.</div>'
            f'<div class="s dn"><div class="t dn">▼ Bajista</div>Perdida de <b class="mono">{fmt(va_l,0)}</b> (VAL) '
            f'→ reabre hacia el minimo <span class="mono">{fmt(lo,0)}</span>.</div>'
            f'<div class="s bl"><div class="t bl">◆ Balance</div>Entre <b class="mono">{fmt(va_l,0)}</b> y '
            f'<b class="mono">{fmt(va_h,0)}</b> → rango; operar los extremos.</div>')

    from html import escape as _esc
    sectors=ctx.get("sectors") or {}; niches=ctx.get("niches") or {}; moves=ctx.get("moves") or {}
    _WT={t:w for (t,w,_s,_i) in COMPONENTS}
    if not sectors:
        sect_html='<div class="news"><div class="ni"><span class="tx1 mut">Sin datos de componentes hoy.</span></div></div>'
        niche_html=movers_html=contrib_html=""
    else:
        srows=""
        for s in sorted(sectors, key=lambda x:-sectors[x][1]):
            rr,wt=sectors[s]; c2="grn" if rr>=0 else "red"; sg="+" if rr>=0 else ""
            bw=min(abs(rr)*100*10,50); side="up" if rr>=0 else "dn"
            srows+=(f'<div class="secrow"><span class="sn">{_esc(s)}</span>'
                    f'<span class="sbar"><span class="mid"></span><i class="{side}" style="width:{bw:.1f}%"></i></span>'
                    f'<span class="sv {c2} mono">{sg}{rr*100:.2f}%</span><span class="sw mut mono">{wt:.1f}%</span></div>')
        sect_html=f'<div class="sect">{srows}</div>'
        big=[(k,v) for k,v in niches.items() if v[1]>=0.30]; big.sort(key=lambda x:-x[1][0])
        lead=big[:5]; lag=big[-5:][::-1]
        def _nr(items):
            o=""
            for (sec,ind),(rr,wt) in items:
                c2="grn" if rr>=0 else "red"; sg="+" if rr>=0 else ""
                o+=f'<div class="nrow"><span class="nn">{_esc(ind)}</span><span class="nv {c2} mono">{sg}{rr*100:.2f}%</span><span class="nw mut mono">{wt:.1f}%</span></div>'
            return o
        niche_html=(f'<div class="niches"><div class="ncol"><div class="nh grn">&#9650; Sub-nichos lideres</div>{_nr(lead)}</div>'
                    f'<div class="ncol"><div class="nh red">&#9660; Sub-nichos rezagados</div>{_nr(lag)}</div></div>')
        ms=sorted(moves.items(), key=lambda x:-x[1]); ups=ms[:7]; downs=ms[-7:][::-1]
        def _mvr(items):
            o=""
            for tk,rr in items:
                c2="grn" if rr>=0 else "red"; sg="+" if rr>=0 else ""
                o+=f'<div class="nrow"><span class="nn mono">{_esc(tk)}</span><span class="nv {c2} mono">{sg}{rr*100:.2f}%</span><span class="nw mut mono">{_WT.get(tk,0):.1f}%</span></div>'
            return o
        movers_html=(f'<div class="niches"><div class="ncol"><div class="nh grn">&#9650; 7 que mas subieron</div>{_mvr(ups)}</div>'
                     f'<div class="ncol"><div class="nh red">&#9660; 7 que mas bajaron</div>{_mvr(downs)}</div></div>')
        contrib=sorted(((tk, _WT.get(tk,0)*rr) for tk,rr in moves.items()), key=lambda x:-x[1])
        cpos=[c for c in contrib if c[1]>0][:7]; cneg=[c for c in contrib if c[1]<0][-7:][::-1]
        def _cb(items):
            o=""
            for tk,cc in items:
                c2="grn" if cc>=0 else "red"; sg="+" if cc>=0 else ""
                o+=f'<div class="nrow"><span class="nn mono">{_esc(tk)}</span><span class="nv {c2} mono">{sg}{cc:.3f} pp</span></div>'
            return o
        contrib_html=(f'<div class="niches"><div class="ncol"><div class="nh grn">&#9650; Impulsaron el indice</div>{_cb(cpos)}</div>'
                      f'<div class="ncol"><div class="nh red">&#9660; Arrastraron el indice</div>{_cb(cneg)}</div></div>')

    badge = '<div class="badge">⚠ PROVISORIO</div>' if ctx.get("provisional") else '<div class="badge ok">✓ CIERRE</div>'
    fecha = ctx["date"].strftime("%A %d de %B de %Y")

    return f"""<!DOCTYPE html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>NQ Key Levels — {ctx['date']}</title><style>{CSS}{EXTRA_CSS}</style></head>
<body><div class="nqr">
  <div class="hd"><div><h1>NASDAQ-100 FUTURES · KEY LEVELS DIARIOS</h1>
  <div class="sub">/MNQ · /NQ — {fecha} · Perfil RTH + Globex</div></div>{badge}</div>
  <div class="headline"><span class="big mono">{fmt(last)}</span>
  <span class="chg {cls} mono">{arrow} {chg:+.2f} ({chgp:+.2f}%)</span>
  <span class="rng mono">Sesion completa · H {fmt(hi,0)} / L {fmt(lo,0)} · rango {fmt(hi-lo,2)} pts</span></div>
  <h3>Desglose por sesion</h3><div class="breakdown">{breakdown}</div>
  <h3>Relato de la sesion</h3><div class="relato">{escape(ctx['narrative'])}</div>
  <h3>Calendario economico (EE.UU.)</h3><div class="news">{cal_html}</div>
  <h3>Titulares del dia</h3><div class="news">{ni}</div>
  <div class="grid2">
    <div><h3>Escalera de niveles</h3><div class="ladder">{ladder}</div></div>
    <div><h3>Tabla de niveles</h3><table><thead><tr><th>Nivel</th><th>Precio</th><th>Tipo</th><th style="text-align:right">Dist.</th></tr></thead><tbody>{rows}</tbody></table></div>
  </div>
  <h3>Escenarios para la proxima rueda</h3><div class="scen">{scen}</div>
  <div class="compsep">— Componentes del Nasdaq-100 —</div>
  <h3>Sectores &middot; hoy (ponderado por peso)</h3>{sect_html}
  <h3>Sub-nichos &middot; lideres y rezagados</h3>{niche_html}
  <h3>Top 7 movers del dia</h3>{movers_html}
  <h3>Mayores aportantes al indice hoy (peso &times; %)</h3>{contrib_html}
  <div class="ft"><span>Generado automaticamente · {dt.datetime.now(ART).strftime('%d/%m/%Y %H:%M')} ART</span>
  <span>Fuente: yfinance + FMP + Finnhub · no es recomendacion de inversion</span></div>
</div></body></html>"""

def render_release_md(ctx):
    f = ctx["full"]; vp = ctx["vp_full"]; last = f["close"]; prev = ctx["prev_close"]
    chg = last - prev; chgp = (chg / prev * 100) if prev else 0; d = ctx["date"].strftime("%Y-%m-%d")
    return "\n".join([
        f"# Nasdaq-100 futures ({fmt(last,2)}, {chgp:+.2f}%) — key levels {d}", "",
        f"Sesion del {d}", "",
        f"- Cierre: {fmt(last)} ({chg:+.2f}, {chgp:+.2f}%)",
        f"- Rango: H {fmt(f['high'],0)} / L {fmt(f['low'],0)} ({fmt(f['high']-f['low'],2)} pts)",
        f"- POC: {fmt(vp['POC'],2)}  ·  Value Area: {fmt(vp['VAL'],0)}-{fmt(vp['VAH'],0)}",
        f"- Resistencias: {fmt(vp['VAH'],0)} (VAH), {fmt(f['high'],0)} (max)",
        f"- Soportes: {fmt(vp['VAL'],0)} (VAL), {fmt(f['low'],0)} (min)", "",
        f"Informe completo: https://{ctx['gh_user']}.github.io/{ctx['gh_repo']}/informes/{d}.html",
    ])

def main():
    full_df, rth_df, on_df, sess_date = load_session()
    ctx = dict(
        full=stats(full_df), rth=stats(rth_df), on=stats(on_df),
        vp_full=volume_profile(full_df), vp_rth=volume_profile(rth_df), vp_on=volume_profile(on_df),
        calendar=fetch_calendar(sess_date), news=fetch_news(), date=sess_date,
        prev_close=float(full_df["Open"].iloc[0]),
        provisional=(dt.datetime.now(NY).time() < dt.time(16, 5) and dt.datetime.now(NY).date() == sess_date),
        gh_user=os.environ.get("GH_USER", "alphainvestment"), gh_repo=os.environ.get("GH_REPO", "Nasdaq"),
    )
    ctx["narrative"] = session_narrative(full_df, rth_df, on_df, ctx["full"], ctx["vp_full"])
    _mv = fetch_component_moves(); ctx["moves"] = _mv; ctx["sectors"], ctx["niches"] = agg_moves(_mv)
    d = sess_date.strftime("%Y-%m-%d"); os.makedirs("informes", exist_ok=True)
    html = render_html(ctx)
    open(f"informes/{d}.html", "w", encoding="utf-8").write(html)
    open("index.html", "w", encoding="utf-8").write(html)
    open("RELEASE_NOTES.md", "w", encoding="utf-8").write(render_release_md(ctx))
    print(f"OK informe {d}: cierre {fmt(ctx['full']['close'])} POC {fmt(ctx['vp_full']['POC'])}")

if __name__ == "__main__":
    main()
