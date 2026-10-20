"""Interactive IMF real and nominal exchange-rate explorer (Python 3.12)."""

from __future__ import annotations

import io
import os
import re
from datetime import date

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pycountry
import requests
import streamlit as st
from babel.numbers import get_territory_currencies
from plotly.subplots import make_subplots


API_BASE = "https://api.imf.org/external/sdmx/3.0"
FIRST_MONTH = "1976-01"
EMPTY_MONTH_INDEX = pd.PeriodIndex([], freq="M", name="Month")


class IMFDataError(Exception):
    """An IMF request returned an error or an unexpected response."""


def api_headers(accept: str) -> dict[str, str]:
    """The IMF gateway may require a subscription key for some deployments."""
    headers = {"Accept": accept}
    key = os.environ.get("IMF_API_KEY")
    try:
        key = st.secrets.get("IMF_API_KEY", key)
    except (FileNotFoundError, AttributeError):
        pass
    if key:
        headers["Ocp-Apim-Subscription-Key"] = str(key)
    return headers


def english_label(value: object, fallback: str) -> str:
    """Read a label from the SDMX-JSON structure (several formats exist)."""
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("en") or value.get("en-US") or next(iter(value.values()), fallback))
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict) and item.get("locale", "en").startswith("en"):
                return str(item.get("value") or item.get("name") or fallback)
        if value:
            item = value[0]
            if isinstance(item, dict):
                return str(item.get("value") or item.get("name") or fallback)
    return fallback


@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def imf_country_options() -> dict[str, str]:
    """Prefer IMF's CL_COUNTRY; fall back to ISO3 names if metadata is unavailable."""
    fallback = {c.alpha_3: c.name for c in pycountry.countries}
    fallback["XKX"] = "Kosovo"
    countries: dict[str, str] = {}

    try:
        response = requests.get(
            f"{API_BASE}/structure/codelist/IMF/CL_COUNTRY/+",
            headers=api_headers("application/vnd.sdmx.structure+json;version=2.0"),
            timeout=(10, 30),
        )
        response.raise_for_status()
        payload = response.json()
        lists = payload.get("data", {}).get("codelists", [])
        if isinstance(lists, dict):
            lists = list(lists.values())
        for code_list in lists:
            if code_list.get("id") != "CL_COUNTRY":
                continue
            codes = code_list.get("codes", [])
            if isinstance(codes, dict):
                codes = list(codes.values())
            for entry in codes:
                code = str(entry.get("id", ""))
                # IMF has aggregate/non-country codes; keep country-like ISO3 codes.
                if code in fallback and re.fullmatch(r"[A-Z]{3}", code):
                    countries[code] = english_label(
                        entry.get("names", entry.get("name")), fallback[code]
                    )
    except (requests.RequestException, ValueError, TypeError, KeyError):
        pass

    # A failed/changed metadata response must not make the country selector empty.
    if len(countries) < 50 or "USA" not in countries or "GBR" not in countries:
        countries = fallback
    countries["G163"] = "Euro area"
    return dict(sorted(countries.items(), key=lambda pair: pair[1].casefold()))


def currency_code(country_code: str) -> str:
    """Current ISO currency for labeling; actual charted values are not rebased."""
    if country_code in {"G163", "XKX"}:
        return "EUR"
    country = pycountry.countries.get(alpha_3=country_code)
    if country is not None:
        codes = get_territory_currencies(country.alpha_2, tender=True)
        if codes:
            return codes[0]
    return f"{country_code} currency"


def monthly_periods(period_values: pd.Series) -> pd.Series:
    """Convert IMF's YYYY-M01 (or YYYY-01) labels to monthly periods."""
    text = period_values.astype("string").str.strip()
    text = text.str.replace(r"^(\d{4})-M(\d{2})$", r"\1-\2", regex=True)
    dates = pd.to_datetime(text, format="%Y-%m", errors="coerce")
    return dates.dt.to_period("M")


@st.cache_data(ttl=24 * 60 * 60, show_spinner=False)
def fetch_imf_series(country_code: str, kind: str) -> pd.Series:
    """Fetch an entire monthly series from Jan 1976, leaving missing months missing.

    CPI: IMF.STA/CPI, COUNTRY.CPI._T.IX.M
    ER:  IMF.STA/ER,  COUNTRY.XDC_USD.PA_RT.M
    Euro area: the IMF reports the all-items HICP in the CPI flow.
    """
    if kind == "cpi":
        flow = "CPI"
        key = (
            f"{country_code}.HICP._T.IX.M"
            if country_code == "G163"
            else f"{country_code}.CPI._T.IX.M"
        )
    elif kind == "fx":
        flow = "ER"
        key = f"{country_code}.XDC_USD.PA_RT.M"
    else:
        raise ValueError(f"Unrecognized kind: {kind}")

    url = f"{API_BASE}/data/dataflow/IMF.STA/{flow}/~/{key}"
    try:
        response = requests.get(
            url,
            params={"startPeriod": FIRST_MONTH},
            headers=api_headers("text/csv"),
            timeout=(10, 45),
        )
    except requests.RequestException as exc:
        raise IMFDataError(f"Network error for {key}: {exc}") from exc

    if response.status_code == 204:
        return pd.Series(index=EMPTY_MONTH_INDEX, dtype=float, name=kind)
    if not response.ok:
        detail = ""
        if response.status_code in (401, 403, 404):
            detail = " Check IMF access, the series key, or the optional IMF_API_KEY."
        if response.status_code == 429:
            detail = " IMF rate limit reached; retry later."
        raise IMFDataError(
            f"IMF HTTP {response.status_code} for {key}.{detail}"
        )

    try:
        frame = pd.read_csv(io.StringIO(response.text), low_memory=False)
    except (ValueError, pd.errors.ParserError, UnicodeError) as exc:
        raise IMFDataError(f"Cannot parse IMF CSV for {key}.") from exc

    frame.columns = frame.columns.str.strip().str.upper()
    if frame.empty:
        return pd.Series(index=EMPTY_MONTH_INDEX, dtype=float, name=kind)
    if not {"TIME_PERIOD", "OBS_VALUE"}.issubset(frame.columns):
        raise IMFDataError(
            f"Unexpected IMF response for {key}: missing TIME_PERIOD / OBS_VALUE."
        )
    if "COUNTRY" in frame.columns:
        frame = frame.loc[frame["COUNTRY"].astype(str) == country_code]

    frame = frame.copy()
    frame["Month"] = monthly_periods(frame["TIME_PERIOD"])
    frame["value"] = pd.to_numeric(frame["OBS_VALUE"], errors="coerce")
    frame = frame.dropna(subset=["Month", "value"])
    frame = frame.loc[np.isfinite(frame["value"]) & (frame["value"] > 0)]
    if frame.empty:
        return pd.Series(index=EMPTY_MONTH_INDEX, dtype=float, name=kind)

    # The requested SDMX key identifies one time series. If the server sends
    # revisions/duplicates, retain the last entry for a given month.
    frame = frame.drop_duplicates(subset="Month", keep="last").sort_values("Month")
    series = frame.set_index("Month")["value"].astype(float)
    series.index = pd.PeriodIndex(series.index, freq="M", name="Month")
    series.name = kind
    return series


def prepare_sample(
    cpi1: pd.Series,
    cpi2: pd.Series,
    fx1: pd.Series,
    fx2: pd.Series,
    start_month: str,
    end_month: str,
) -> tuple[pd.DataFrame, float]:
    """Calculate S, RER, and the *sample-specific* mean-normalized real index."""
    months = pd.period_range(start_month, end_month, freq="M", name="Month")
    result = pd.DataFrame(index=months)
    for column, series in (
        ("P1_CPI", cpi1),
        ("P2_CPI", cpi2),
        ("FX1_local_per_USD", fx1),
        ("FX2_local_per_USD", fx2),
    ):
        result[column] = series.reindex(months)

    # One Country 1 unit = 1/fx1 USD; each USD buys fx2 Country 2 units.
    result["S_currency2_per_currency1"] = (
        result["FX2_local_per_USD"] / result["FX1_local_per_USD"]
    )
    result["RER"] = (
        result["S_currency2_per_currency1"]
        * result["P1_CPI"]
        / result["P2_CPI"]
    )
    result = result.replace([np.inf, -np.inf], np.nan)
    # A nominal observation needs only both FX values; a real observation
    # also needs both CPI levels. Keep nominal data when CPI is missing.
    valid_fx = result[["FX1_local_per_USD", "FX2_local_per_USD"]].gt(0).all(axis=1)
    valid_inputs = result[
        ["P1_CPI", "P2_CPI", "FX1_local_per_USD", "FX2_local_per_USD"]
    ].gt(0).all(axis=1)
    result.loc[~valid_fx, "S_currency2_per_currency1"] = np.nan
    result.loc[~valid_inputs, "RER"] = np.nan
    mean_rer = float(result["RER"].mean())  # skips missing months
    result["RER_sample_mean"] = mean_rer if np.isfinite(mean_rer) else np.nan
    result["Real_Index_100"] = (
        100 * result["RER"] / mean_rer
        if np.isfinite(mean_rer) and mean_rer > 0
        else np.nan
    )
    return result, mean_rer


def plot_sample(sample: pd.DataFrame, mode: str, currency1: str, currency2: str) -> go.Figure:
    x = sample.index.to_timestamp()
    real = go.Scatter(
        x=x,
        y=sample["Real_Index_100"],
        mode="lines",
        name="Real index (mean = 100)",
        line={"color": "#1d4ed8", "width": 2.5},
        hovertemplate="%{x|%b %Y}<br>Real index: %{y:,.2f}<extra></extra>",
        connectgaps=False,
    )
    nominal = go.Scatter(
        x=x,
        y=sample["S_currency2_per_currency1"],
        mode="lines",
        name=f"Nominal ({currency2}/{currency1})",
        line={"color": "#ea580c", "width": 2.3},
        hovertemplate=f"%{{x|%b %Y}}<br>{currency2} per {currency1}: %{{y:,.6g}}<extra></extra>",
        connectgaps=False,
    )
    nominal_label = f"Nominal rate ({currency2} per {currency1})"

    if mode == "Both":
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        fig.add_trace(real, secondary_y=False)
        fig.add_trace(nominal, secondary_y=True)
        fig.update_yaxes(title_text="Real index (sample mean = 100)", secondary_y=False)
        fig.update_yaxes(title_text=nominal_label, secondary_y=True)
    else:
        fig = go.Figure()
        if mode == "Real":
            fig.add_trace(real)
            fig.update_yaxes(title_text="Real index (sample mean = 100)")
        else:
            fig.add_trace(nominal)
            fig.update_yaxes(title_text=nominal_label)

    fig.update_layout(
        template="plotly_white",
        height=510,
        margin=dict(l=20, r=25, t=25, b=20),
        hovermode="x unified",
        legend=dict(orientation="h", y=1.12, x=0),
        xaxis_title="Month",
    )
    return fig


def main() -> None:
    st.set_page_config(page_title="IMF Real Exchange Rate Explorer", page_icon="🌍", layout="wide")
    st.title("IMF Real & Nominal Exchange Rate Explorer")
    st.caption("Monthly IMF data • available observations from January 1976 • sample-specific RER normalization")

    labels = imf_country_options()
    codes = list(labels)
    default1 = codes.index("USA") if "USA" in codes else 0
    default2 = codes.index("GBR") if "GBR" in codes else min(1, len(codes) - 1)
    format_country = lambda code: f"{labels[code]} ({code})"

    col1, col2, col3 = st.columns([1.2, 1.2, 1])
    with col1:
        country1 = st.selectbox(
            "Country 1 (one unit of this currency)",
            codes,
            index=default1,
            format_func=format_country,
        )
    with col2:
        country2 = st.selectbox(
            "Country 2 (units of this currency)",
            codes,
            index=default2,
            format_func=format_country,
        )
    with col3:
        mode = st.radio("Chart view", ["Both", "Real", "Nominal"], horizontal=True)

    months = pd.period_range(FIRST_MONTH, date.today().strftime("%Y-%m"), freq="M").astype(str).tolist()
    start_default = months.index("2000-01") if "2000-01" in months else 0
    start_col, end_col = st.columns(2)
    with start_col:
        start_month = st.selectbox("Start month", months, index=start_default)
    with end_col:
        end_month = st.selectbox("End month", months, index=len(months) - 1)

    if start_month > end_month:
        st.error("Start month must be on or before end month.")
        st.stop()

    ccy1 = currency_code(country1)
    ccy2 = currency_code(country2)
    st.markdown(
        f"**Definition:** S(t) = units of **{ccy2}** per 1 **{ccy1}**; "
        "RER(t) = S(t) × P1(t) / P2(t); "
        "Real Index(t) = 100 × RER(t) / average sample RER."
    )
    if country1 == country2:
        st.info("You selected the same economy twice; the nominal rate should be 1 and the real index 100 where data are available.")

    with st.spinner("Retrieving IMF CPI and monthly exchange-rate observations…"):
        requested = {
            "Country 1 CPI": (country1, "cpi"),
            "Country 2 CPI": (country2, "cpi"),
            "Country 1 FX": (country1, "fx"),
            "Country 2 FX": (country2, "fx"),
        }
        loaded: dict[str, pd.Series] = {}
        problems: list[str] = []
        for label, (code, kind) in requested.items():
            try:
                loaded[label] = fetch_imf_series(code, kind)
                if loaded[label].empty:
                    problems.append(f"{label}: no usable monthly observations returned for {code}.")
            except IMFDataError as exc:
                loaded[label] = pd.Series(index=EMPTY_MONTH_INDEX, dtype=float)
                problems.append(f"{label}: {exc}")

    # Show errors without fabricating or interpolating missing data.
    if problems:
        with st.expander("Data availability / IMF API messages", expanded=True):
            for issue in problems:
                st.warning(issue)

    sample, rer_mean = prepare_sample(
        loaded["Country 1 CPI"],
        loaded["Country 2 CPI"],
        loaded["Country 1 FX"],
        loaded["Country 2 FX"],
        start_month,
        end_month,
    )
    valid_count = int(sample["RER"].notna().sum())
    nominal_count = int(sample["S_currency2_per_currency1"].notna().sum())
    stat1, stat2, stat3 = st.columns(3)
    stat1.metric("Valid RER months", f"{valid_count} / {len(sample)}")
    stat2.metric("RER* (mean over valid months)", f"{rer_mean:,.6g}" if np.isfinite(rer_mean) else "N/A")
    stat3.metric("Nominal-rate months", f"{nominal_count} / {len(sample)}")

    has_real = valid_count > 0
    has_nominal = nominal_count > 0
    if (mode in {"Real", "Both"} and has_real) or (mode in {"Nominal", "Both"} and has_nominal):
        fig = plot_sample(sample, mode, ccy1, ccy2)
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No usable observations to chart for this view and period. Try different countries or months.")

    if not has_real:
        st.info("No months have all four positive input values, so the sample RER mean and real index are unavailable.")
    elif valid_count < len(sample):
        st.caption("Missing months are kept as blanks and excluded from the RER* average; no interpolation is performed.")

    export = sample.reset_index()
    export["Month"] = export["Month"].astype(str)
    st.download_button(
        "Download selected sample (CSV)",
        data=export.to_csv(index=False).encode("utf-8"),
        file_name=f"imf_rer_{country1}_{country2}_{start_month}_{end_month}.csv",
        mime="text/csv",
    )
    with st.expander("Show the monthly observations and calculations"):
        st.dataframe(export, use_container_width=True, hide_index=True)

    with st.expander("Data sources and calculation notes"):
        st.markdown(
            "- **Source:** IMF SDMX 3.0, dataflows `IMF.STA/CPI` and `IMF.STA/ER`. "
            "All data are fetched starting January 1976 where available.\n"
            "- **CPI keys:** `{ISO3}.CPI._T.IX.M`. For the Euro area (`G163`), "
            "use the all-items harmonised CPI key `G163.HICP._T.IX.M`.\n"
            "- **Exchange-rate keys:** `{ISO3}.XDC_USD.PA_RT.M` (monthly period-average "
            "local currency per USD).\n"
            "- **Direction:** if `FX1` and `FX2` are local currency per USD, "
            "then `S(t) = FX2(t) / FX1(t)`, i.e. Country 2 units for one Country 1 unit.\n"
            "- **Normalization:** `RER*` is the arithmetic mean of valid monthly `RER` "
            "**inside the chosen date range**. Changing dates recomputes it. "
            "The nominal series is never indexed or normalized.\n"
            "- **Missing data:** all months are retained in CSV; missing or nonpositive "
            "inputs are not interpolated. Availability varies by economy and month.\n"
            "- **Currency caveat:** labels show current currency codes. Historical currency "
            "changes/redenominations can affect nominal-series comparability; the euro "
            "did not exist as a trading currency over the full 1976-present window."
        )
        st.link_button("IMF data portal", "https://data.imf.org/")


if __name__ == "__main__":
    main()
