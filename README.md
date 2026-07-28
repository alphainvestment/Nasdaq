# Nasdaq-100 Futures — Key Levels diarios

Informe automatico de niveles clave del futuro Nasdaq-100 (/NQ). Corre solo cada
dia habil a las **18:30 ART**, publica el HTML en GitHub Pages y manda un mail
(via GitHub Release) con el resumen — mismo esquema que el repo `Internacional`.

## Que hace

1. Baja barras de 1 minuto de `NQ=F` con **yfinance** (solo la ultima sesion).
2. Separa **RTH** (9:30-16:00 NY) de **Globex / overnight** y calcula, por franja,
   el perfil de volumen: **POC, VAH, VAL**, rango y volumen relativo.
3. Trae los **titulares del dia** de Finnhub (categoria general, filtrados por mercado).
4. Arma la **escalera de niveles**, la **tabla** y los **escenarios** para la proxima rueda.
5. Genera `informes/AAAA-MM-DD.html` + `index.html` y crea un **Release** que te llega por mail.

## Setup (una sola vez)

1. **Crear el repo** en tu cuenta (ej. `alphainvestment/Nasdaq`) y subir estos archivos:
   `generate_report.py`, `report.css`, `requirements.txt`, `.github/workflows/daily-report.yml`.

2. **API key de Finnhub** (gratis en finnhub.io):
   `Settings -> Secrets and variables -> Actions -> New repository secret`
   - Name: `FINNHUB_KEY`
   - Value: tu key.

3. **Activar GitHub Pages**: `Settings -> Pages -> Source: Deploy from a branch ->
   Branch: main / (root)`. El informe queda en
   `https://TU_USUARIO.github.io/TU_REPO/informes/AAAA-MM-DD.html`.

4. **Permisos del workflow**: `Settings -> Actions -> General -> Workflow permissions ->
   Read and write permissions`.

5. **Suscribirte al repo** (Watch -> All Activity) para recibir el mail de cada Release.

## Probarlo ya

`Actions -> Informe diario Nasdaq Key Levels -> Run workflow`. En 1-2 min tenes el
informe publicado y el mail en tu casilla.

## Notas y limitaciones

- El perfil de volumen se calcula con **volumen por barra de 1m** (yfinance), asi que
  el POC/value area son una **muy buena aproximacion** pero no identicos a thinkorswim
  (que usa tick-level). Para precision exacta -> migrar a Databento (etapa 2).
- `prev_close` hoy usa el open de la sesion como proxy del cierre previo; se puede
  mejorar bajando el cierre del dia anterior (etapa 2).
- Niveles de dias previos (PDH/PDL, naked POC, max/min semanal) quedan para **etapa 2**.
- No es recomendacion de inversion.
