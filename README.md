# Real-exchange-rate-app-created-by-Juzheng-Chen
This is for the FIN470 project
IMF Real & Nominal Exchange Rate Explorer
A Streamlit application for exploring monthly bilateral exchange rates and CPI-adjusted real exchange rates, using the IMF SDMX 3.0 API. Compatible with Python 3.12.
Features
Choose Country 1, Country 2, or the Euro area (G163) from the economy selectors.
Request monthly IMF CPI and foreign-exchange-rate data beginning January 1976, where available.
Select start and end months. Recalculate the real exchange rate's sample mean each time dates change.
Switch between Real, Nominal, and Both. In Both mode, the lines use different y-axes.
Display nominal exchange rates in actual currency units, not indexed to 100.
Download a CSV for every month in the selected range, including input data, intermediate calculations, and blanks for missing observations.
Review IMF availability and network/API errors in the app instead of silently substituting data.
Install and run
Install Python 3.12.
Place `app.py`, `requirements.txt`, and `README.md` in the same folder.
In a terminal in that folder, run:
```bash
   python -m venv .venv
   ```
Activate the environment:
macOS/Linux: `source .venv/bin/activate`
Windows PowerShell: `.venv\Scripts\Activate.ps1`
Install dependencies and launch the app:
```bash
   python -m pip install -r requirements.txt
   python -m streamlit run app.py
   ```
Streamlit prints the local URL (typically `http://localhost:8501`). Internet access is required to request IMF data. The app caches IMF responses for 24 hours and computes the selected-date calculations locally.
Optional IMF API key
The IMF gateway's authentication policies can vary. If your requests require a subscription key, supply it as an environment variable named `IMF_API_KEY` or as a Streamlit secret:
```toml
# .streamlit/secrets.toml (do not commit a real key)
IMF_API_KEY = "your-key-here"
```
Do not put credentials in `app.py` or commit them to version control. If you receive HTTP 401/403/404 responses, confirm the API is accessible and that the requested series exists; an HTTP 404 can also arise from unavailable data or gateway restrictions.
Formulae and quote direction
Let:
`P1(t)` = Country 1 monthly consumer price index.
`P2(t)` = Country 2 monthly consumer price index.
`FX1(t)` = Country 1 currency units per one USD (IMF `XDC_USD`).
`FX2(t)` = Country 2 currency units per one USD.
`S(t)` = Country 2 currency units per one Country 1 currency unit.
The IMF's USD quotations must be crossed in the following direction:
```text
S(t)          = FX2(t) / FX1(t)
RER(t)        = S(t) * P1(t) / P2(t)
RER*          = arithmetic mean of RER(t) for valid months in selected range
Real Index(t) = 100 * RER(t) / RER*
```
Example: If `FX1 = 1 USD/USD` and `FX2 = 150 JPY/USD`, then with the United States as Country 1 and Japan as Country 2, `S = 150 JPY/USD`—not `1/150`.
Changing the start or end month recomputes `RER*`; the real index is rebased to a sample mean of 100. The nominal rate `S(t)` is never rebased.
Invalid and unavailable values are excluded from the RER mean; nothing is interpolated. All selected months remain in the CSV with blank cells for unavailable quantities.
IMF SDMX 3.0 series
Endpoint pattern:
```text
https://api.imf.org/external/sdmx/3.0/data/dataflow/IMF.STA/{FLOW}/~/{KEY}?startPeriod=1976-01
```
Variable	Dataflow (`FLOW`)	Series key (`KEY`)	Meaning
CPI	`CPI`	`{ISO3}.CPI._T.IX.M`	Headline monthly national CPI index
Euro-area CPI	`CPI`	`G163.HICP._T.IX.M`	Euro-area all-items Harmonised CPI (HICP)
FX	`ER`	`{ISO3}.XDC_USD.PA_RT.M`	Monthly average domestic currency units per USD
The app requests `text/csv` and parses the `TIME_PERIOD` and `OBS_VALUE` fields. It uses the IMF's country codelist when available, falling back to ISO-3 economy names if metadata access is unavailable. Available CPI, FX, and Euro-area histories do not necessarily begin in 1976, and some country/series combinations are unavailable.
Important history caveat: National currencies may have changed or been redenominated, and the euro did not exist throughout the entire requested period. Historical nominal-rate levels may therefore not be directly comparable across currency-regime changes. Currency labels are based on current ISO currency codes; they do not alter historical numeric rates.
Output columns
Column	Description
`Month`	Monthly observation (`YYYY-MM`)
`P1_CPI`, `P2_CPI`	CPI levels for Country 1 and 2
`FX1_local_per_USD`, `FX2_local_per_USD`	Raw IMF national-currency-per-USD values
`S_currency2_per_currency1`	Cross rate `FX2/FX1`
`RER`	Raw bilateral real exchange rate `S * P1/P2`
`RER_sample_mean`	Mean RER computed from the selected valid sample months
`Real_Index_100`	CPI-adjusted series rebased to a sample mean of 100
Troubleshooting
No matching observations: Choose another country pair or a more recent date range. The app does not invent or interpolate values.
HTTP 429: The IMF rate limit was reached. Retry later.
HTTP 401/403/404: Check the IMF API, requested key, and whether an API subscription key is necessary.
Unexpected response format: The IMF may have changed its SDMX CSV output. Check that the API provides `TIME_PERIOD` and `OBS_VALUE` columns.
Data provider: International Monetary Fund. API background: IMF API.
