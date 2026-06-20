# -*- coding: utf-8 -*-
"""
usd_chf_cip.py

Builds monthly USD/CHF covered interest parity (CIP) deviation measures from
public SNB and FRED data, then saves data, diagnostics, and a publication-style
chart.
"""

from __future__ import annotations

import io
import os
import re
import unicodedata
from dataclasses import dataclass
from typing import Iterable

import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import requests


CONFIG = {
    "START_DATE": "1990-01-01",
    "SNB_FX_URL": "https://data.snb.ch/api/cube/devkum/data/json/en",
    "SNB_RATES_URL": "https://data.snb.ch/api/cube/zimoma/data/json/en",
    "SNB_SARON_URL": "https://data.snb.ch/api/cube/zirepo/data/json/en",
    "DATA_OUTPUT_PATH": "data/usd_chf_cip_deviations_monthly.csv",
    "DIAGNOSTICS_OUTPUT_PATH": "data/source_diagnostics.csv",
    "CHART_OUTPUT_PATH": "chart/usd_chf_cip_deviations.png",
    "BENCHMARK_OUTPUT_PATH": "data/du_keerati_schreger_chf_cip_monthly.csv",
    "VALIDATION_OUTPUT_PATH": "data/benchmark_validation.csv",
    "DKS_CIP_URL": "https://jschreger.s3.us-east-2.amazonaws.com/cip_dataset_v4.csv",
    "DOWNLOAD_TIMEOUT_SECONDS": 30,
}


@dataclass(frozen=True)
class SourceRecord:
    name: str
    source: str
    source_id: str
    start: str
    end: str
    observations: int
    status: str


def set_custom_style() -> list[str]:
    """Applies the project plotting palette and Matplotlib style."""
    palette = [
        "#00466F",
        "#F38C10",
        "#3297DB",
        "#037E73",
        "#C62828",
        "#FEBD00",
        "#41B01E",
        "#E84C3D",
        "#3D3D3D",
    ]

    plt.style.use("default")
    plt.rcParams.update(
        {
            "axes.titlesize": 12,
            "axes.labelsize": 10,
            "axes.edgecolor": "black",
            "axes.linewidth": 1,
            "axes.grid": False,
            "xtick.labelsize": 9,
            "ytick.labelsize": 9,
            "legend.fontsize": 7,
            "legend.frameon": False,
            "font.size": 10,
            "lines.linewidth": 1.5,
            "lines.color": "black",
            "figure.figsize": (6, 4),
            "figure.dpi": 300,
            "axes.prop_cycle": plt.cycler(color=palette),
        }
    )
    return palette


def clean_text(value: str) -> str:
    """Normalizes API labels for robust matching and diagnostics."""
    value = str(value)
    for char in ("\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2212", "\ufffd"):
        value = value.replace(char, "-")
    return re.sub(r"\s+", " ", value).strip()

def normalize_label(value: str) -> str:
    """Normalizes labels for robust matching across API dash/spacing variants."""
    value = unicodedata.normalize("NFKD", clean_text(value))
    return value.casefold()
def request_json(url: str) -> dict:
    response = requests.get(url, timeout=CONFIG["DOWNLOAD_TIMEOUT_SECONDS"])
    response.raise_for_status()
    return response.json()


def choose_label(labels: dict, candidates: Iterable[str]) -> tuple[str, str]:
    """Returns the API key and label matching one of the candidate labels."""
    normalized = {key: normalize_label(label) for key, label in labels.items()}
    candidate_norms = [normalize_label(candidate) for candidate in candidates]

    for candidate in candidate_norms:
        for key, label in normalized.items():
            if label == candidate:
                return key, labels[key]

    for candidate in candidate_norms:
        for key, label in normalized.items():
            if candidate in label or label in candidate:
                return key, labels[key]

    examples = "; ".join(list(labels.values())[:8])
    raise ValueError(f"No label matched {list(candidates)}. Example labels: {examples}")


def snb_json_series(url: str, candidates: Iterable[str], name: str) -> tuple[pd.Series, SourceRecord]:
    """Fetches one monthly SNB series from a cube/timeseries JSON response."""
    data = request_json(url)

    if "dataset" in data:
        dim = data["dataset"]["dimension"]
        series_labels = dim["D1"]["category"]["label"]
        date_labels = dim["D0"]["category"]["label"]
        series_id, matched_label = choose_label(series_labels, candidates)
        key = f"{series_id}:0:0:0"
        series_block = data["dataset"].get("series", {}).get(key)
        if series_block is None:
            raise KeyError(f"SNB key '{key}' not found for {matched_label}.")
        records = [
            {"Date": date_labels[idx], "Value": values[0]}
            for idx, values in series_block.get("observations", {}).items()
        ]
    elif "timeseries" in data:
        target = None
        matched_label = ""
        for series in data["timeseries"]:
            labels = {str(i): header.get("dimItem", "") for i, header in enumerate(series.get("header", []))}
            try:
                _, matched_label = choose_label(labels, candidates)
                target = series
                break
            except ValueError:
                continue
        if target is None:
            raise ValueError(f"SNB series not found: {list(candidates)}")
        records = [{"Date": row["date"], "Value": row["value"]} for row in target["values"]]
    elif "header" in data and "values" in data:
        labels = {str(i): header.get("dimItem", "") for i, header in enumerate(data.get("header", []))}
        _, matched_label = choose_label(labels, candidates)
        records = [{"Date": row["date"], "Value": row["value"]} for row in data["values"]]
    else:
        raise ValueError("Unknown SNB JSON format.")

    series = records_to_monthly_series(records, name)
    return series, make_record(name, "SNB", matched_label, series)


def fred_csv(series_id: str, name: str, start: str = "1990-01-01") -> tuple[pd.Series, SourceRecord]:
    """Fetches one FRED series from the public CSV endpoint."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={start}"
    response = requests.get(url, timeout=CONFIG["DOWNLOAD_TIMEOUT_SECONDS"])
    response.raise_for_status()
    frame = pd.read_csv(io.StringIO(response.text))
    date_col = "DATE" if "DATE" in frame.columns else "observation_date"
    if date_col not in frame.columns:
        raise ValueError(f"FRED date column not found for {series_id}.")
    records = frame.rename(columns={date_col: "Date", series_id: "Value"})[["Date", "Value"]]
    series = records_to_daily_or_monthly_series(records, name)
    return series, make_record(name, "FRED", series_id, series)


def records_to_monthly_series(records: Iterable[dict], name: str) -> pd.Series:
    frame = pd.DataFrame.from_records(records)
    if frame.empty:
        return pd.Series(dtype="float64", name=name)
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    frame["Value"] = pd.to_numeric(frame["Value"], errors="coerce")
    frame = frame.dropna(subset=["Date", "Value"])
    series = (
        frame.assign(Date=frame["Date"].dt.to_period("M").dt.to_timestamp("M"))
        .groupby("Date")["Value"]
        .last()
        .sort_index()
    )
    return series[series.index >= pd.Timestamp(CONFIG["START_DATE"])].rename(name)


def records_to_daily_or_monthly_series(records: pd.DataFrame, name: str) -> pd.Series:
    frame = records.copy()
    frame["Date"] = pd.to_datetime(frame["Date"], errors="coerce")
    frame["Value"] = pd.to_numeric(frame["Value"], errors="coerce")
    frame = frame.dropna(subset=["Date", "Value"])
    series = frame.set_index("Date")["Value"].sort_index().rename(name)
    return series[series.index >= pd.Timestamp(CONFIG["START_DATE"])]


def make_record(name: str, source: str, source_id: str, series: pd.Series) -> SourceRecord:
    if series.empty:
        return SourceRecord(name, source, clean_text(source_id), "", "", 0, "empty")
    return SourceRecord(
        name=name,
        source=source,
        source_id=clean_text(source_id),
        start=series.index.min().date().isoformat(),
        end=series.index.max().date().isoformat(),
        observations=int(series.notna().sum()),
        status="ok",
    )



def dks_chf_govt_cip_monthly() -> tuple[pd.Series, pd.DataFrame, SourceRecord]:
    """Loads the published CHF 3M government-bond CIP benchmark.

    Du, Keerati, and Schreger define cip_govt as the foreign government
    yield minus the forward premium minus the matched US Treasury yield. The
    sign-adjusted series matches this repository's USD-minus-CHF convention.
    """
    columns = ["currency", "tenor", "date", "diff_y", "rho", "cip_govt"]
    chunks: list[pd.DataFrame] = []
    for chunk in pd.read_csv(CONFIG["DKS_CIP_URL"], usecols=columns, chunksize=250_000):
        subset = chunk[chunk["currency"].eq("CHF") & chunk["tenor"].eq("3m")].copy()
        if not subset.empty:
            chunks.append(subset)

    if chunks:
        daily = pd.concat(chunks, ignore_index=True)
        daily["Date"] = pd.to_datetime(daily["date"], format="%d%b%Y", errors="coerce")
        daily["dks_diff_y_pct"] = pd.to_numeric(daily["diff_y"], errors="coerce")
        daily["dks_rho_pct"] = pd.to_numeric(daily["rho"], errors="coerce")
        daily["dks_cip_govt_original_bps"] = pd.to_numeric(daily["cip_govt"], errors="coerce")
        daily["dks_chf_govt_cip_3m_bps"] = -daily["dks_cip_govt_original_bps"]
        daily = daily.dropna(subset=["Date", "dks_chf_govt_cip_3m_bps"])
        daily = daily.sort_values("Date")
        benchmark = (
            daily.assign(Date=daily["Date"].dt.to_period("M").dt.to_timestamp("M"))
            .groupby("Date")[[
                "dks_diff_y_pct",
                "dks_rho_pct",
                "dks_cip_govt_original_bps",
                "dks_chf_govt_cip_3m_bps",
            ]]
            .last()
            .sort_index()
        )
    else:
        benchmark = pd.DataFrame(
            columns=[
                "dks_diff_y_pct",
                "dks_rho_pct",
                "dks_cip_govt_original_bps",
                "dks_chf_govt_cip_3m_bps",
            ]
        )

    benchmark = benchmark[benchmark.index >= pd.Timestamp(CONFIG["START_DATE"])].copy()
    monthly = benchmark["dks_chf_govt_cip_3m_bps"].rename("dks_chf_govt_cip_3m_bps")
    benchmark["source_series"] = "Du-Keerati-Schreger cip_govt, CHF 3M"
    record = make_record(
        "dks_chf_govt_cip_3m_bps",
        "Du-Keerati-Schreger",
        "cip_dataset_v4.csv: CHF 3m cip_govt, sign adjusted",
        monthly,
    )
    return monthly, benchmark, record

def percent_to_continuous_pa(series: pd.Series, name: str) -> pd.Series:
    """Converts percent p.a. to continuously compounded p.a."""
    monthly = series.copy()
    monthly.index = monthly.index.to_period("M").to_timestamp("M")
    return np.log1p(monthly / 100.0).rename(name)


def sofr_compounded_rate(sofr_index: pd.Series, days: int, name: str) -> pd.Series:
    """Computes an annualized continuously compounded rate from the SOFR index."""
    daily = sofr_index.asfreq("D").ffill()
    rate = np.log(daily / daily.shift(days)) * (360.0 / days)
    return rate.resample("ME").last().rename(name)


def forward_implied_differential(spot: pd.Series, forward: pd.Series, tenor_years: float) -> pd.Series:
    """For CHF per USD quotes, returns the forward-implied USD minus CHF rate differential."""
    return (-np.log(forward / spot) / tenor_years).rename("forward_implied_usd_chf_diff")


def build_outputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    diagnostics: list[SourceRecord] = []

    spot, record = snb_json_series(CONFIG["SNB_FX_URL"], ["America - United States - USD 1"], "spot_chf_per_usd")
    diagnostics.append(record)
    fwd3m, record = snb_json_series(CONFIG["SNB_FX_URL"], ["USD Forward rates - US Dollar 3-month forward rate (CHF per 1 USD)"], "forward_3m_chf_per_usd")
    diagnostics.append(record)
    fwd6m, record = snb_json_series(CONFIG["SNB_FX_URL"], ["USD Forward rates - US Dollar 6-month forward rate (CHF per 1 USD)"], "forward_6m_chf_per_usd")
    diagnostics.append(record)

    sofr_index, record = fred_csv("SOFRINDEX", "sofr_index", CONFIG["START_DATE"])
    diagnostics.append(record)
    sofr_3m = sofr_compounded_rate(sofr_index, 90, "usd_sofr_3m")
    sofr_6m = sofr_compounded_rate(sofr_index, 180, "usd_sofr_6m")

    saron_3m_raw, record = snb_json_series(CONFIG["SNB_SARON_URL"], ["SARON 3M Compound Rate"], "saron_3m_percent")
    diagnostics.append(record)
    saron_6m_raw, record = snb_json_series(CONFIG["SNB_SARON_URL"], ["SARON 6M Compound Rate"], "saron_6m_percent")
    diagnostics.append(record)
    saron_3m = percent_to_continuous_pa(saron_3m_raw, "chf_saron_3m")
    saron_6m = percent_to_continuous_pa(saron_6m_raw, "chf_saron_6m")

    usd_libor_3m_raw, record = snb_json_series(CONFIG["SNB_RATES_URL"], ["United States - USD - USD LIBOR - 3-month"], "usd_libor_3m_percent")
    diagnostics.append(record)
    chf_libor_3m_raw, record = snb_json_series(CONFIG["SNB_RATES_URL"], ["Switzerland - CHF - CHF LIBOR - 3-month"], "chf_libor_3m_percent")
    diagnostics.append(record)
    usd_libor_3m = percent_to_continuous_pa(usd_libor_3m_raw, "usd_libor_3m")
    chf_libor_3m = percent_to_continuous_pa(chf_libor_3m_raw, "chf_libor_3m")

    usd_gov_3m_raw, record = fred_csv("TB3MS", "usd_tbill_3m_percent", CONFIG["START_DATE"])
    diagnostics.append(record)
    chf_gov_3m_raw, record = snb_json_series(
        CONFIG["SNB_RATES_URL"],
        ["Switzerland - CHF - Money market debt register claims of the Swiss Confederation - 3-month"],
        "chf_confederation_money_market_3m_percent",
    )
    diagnostics.append(record)
    usd_gov_3m = percent_to_continuous_pa(usd_gov_3m_raw.resample("ME").last(), "usd_tbill_3m")
    chf_gov_3m = percent_to_continuous_pa(chf_gov_3m_raw, "chf_confederation_money_market_3m")

    dks_chf_govt_cip_3m, benchmark_frame, record = dks_chf_govt_cip_monthly()
    diagnostics.append(record)

    panel = pd.concat(
        [
            spot,
            fwd3m,
            fwd6m,
            sofr_3m,
            sofr_6m,
            saron_3m,
            saron_6m,
            usd_libor_3m,
            chf_libor_3m,
            usd_gov_3m,
            chf_gov_3m,
            dks_chf_govt_cip_3m,
        ],
        axis=1,
    )

    panel["forward_implied_usd_chf_3m"] = forward_implied_differential(panel["spot_chf_per_usd"], panel["forward_3m_chf_per_usd"], 0.25)
    panel["forward_implied_usd_chf_6m"] = forward_implied_differential(panel["spot_chf_per_usd"], panel["forward_6m_chf_per_usd"], 0.50)
    panel["cip_basis_sofr_saron_3m_bps"] = 10000.0 * ((panel["usd_sofr_3m"] - panel["chf_saron_3m"]) - panel["forward_implied_usd_chf_3m"])
    panel["cip_basis_sofr_saron_6m_bps"] = 10000.0 * ((panel["usd_sofr_6m"] - panel["chf_saron_6m"]) - panel["forward_implied_usd_chf_6m"])
    panel["cip_basis_libor_3m_bps"] = 10000.0 * ((panel["usd_libor_3m"] - panel["chf_libor_3m"]) - panel["forward_implied_usd_chf_3m"])
    panel["cip_basis_government_3m_bps"] = 10000.0 * ((panel["usd_tbill_3m"] - panel["chf_confederation_money_market_3m"]) - panel["forward_implied_usd_chf_3m"])

    panel = drop_incomplete_current_month(panel)

    output_columns = [
        "spot_chf_per_usd",
        "forward_3m_chf_per_usd",
        "forward_6m_chf_per_usd",
        "forward_implied_usd_chf_3m",
        "forward_implied_usd_chf_6m",
        "usd_sofr_3m",
        "chf_saron_3m",
        "usd_sofr_6m",
        "chf_saron_6m",
        "usd_libor_3m",
        "chf_libor_3m",
        "usd_tbill_3m",
        "chf_confederation_money_market_3m",
        "cip_basis_sofr_saron_3m_bps",
        "cip_basis_sofr_saron_6m_bps",
        "cip_basis_libor_3m_bps",
        "cip_basis_government_3m_bps",
        "dks_chf_govt_cip_3m_bps",
    ]
    diagnostics_frame = pd.DataFrame([record.__dict__ for record in diagnostics])
    output_panel = panel[output_columns]
    validation_frame = benchmark_validation(output_panel)
    return output_panel, diagnostics_frame, benchmark_frame, validation_frame


def drop_incomplete_current_month(panel: pd.DataFrame) -> pd.DataFrame:
    """Drops the current month-end label until the month has actually closed."""
    today = pd.Timestamp.today().normalize()
    current_month_end = today + pd.offsets.MonthEnd(0)
    if today < current_month_end:
        return panel[panel.index < current_month_end]
    return panel

def benchmark_validation(panel: pd.DataFrame) -> pd.DataFrame:
    """Summarizes correlations with the external CHF 3M government-bond benchmark."""
    benchmark = "dks_chf_govt_cip_3m_bps"
    candidates = {
        "government_rates_3m": "cip_basis_government_3m_bps",
        "libor_3m": "cip_basis_libor_3m_bps",
        "sofr_saron_3m": "cip_basis_sofr_saron_3m_bps",
    }
    windows = {
        "full_overlap": (None, None),
        "pre_2022_overlap": ("2008-01-01", "2021-12-31"),
        "post_2015_pre_2022": ("2015-01-01", "2021-12-31"),
    }
    rows = []
    for measure_name, measure in candidates.items():
        for window_name, (start, end) in windows.items():
            sample = panel[[measure, benchmark]].dropna()
            if start is not None:
                sample = sample.loc[pd.Timestamp(start) : pd.Timestamp(end)]
            rows.append(
                {
                    "measure": measure_name,
                    "benchmark": "Du-Keerati-Schreger CHF 3M government-bond CIP, sign adjusted",
                    "window": window_name,
                    "start": sample.index.min().date().isoformat() if not sample.empty else "",
                    "end": sample.index.max().date().isoformat() if not sample.empty else "",
                    "observations": int(len(sample)),
                    "correlation": sample.iloc[:, 0].corr(sample.iloc[:, 1]) if len(sample) > 2 else np.nan,
                }
            )
    return pd.DataFrame(rows)

def plot_outputs(panel: pd.DataFrame) -> None:
    palette = set_custom_style()
    constructed_columns = {
        "cip_basis_sofr_saron_3m_bps": "SOFR-SARON (3M)",
        "cip_basis_sofr_saron_6m_bps": "SOFR-SARON (6M)",
        "cip_basis_libor_3m_bps": "LIBOR (3M)",
        "cip_basis_government_3m_bps": "Government rates (3M)",
    }
    benchmark_column = "dks_chf_govt_cip_3m_bps"
    benchmark_label = "D-K-S govt bond (3M)"

    fig, ax = plt.subplots(figsize=(6, 4))
    for idx, (column, label) in enumerate(constructed_columns.items()):
        ax.plot(panel.index, panel[column], label=label, color=palette[idx], linewidth=1.2)

    ax.plot(
        panel.index,
        panel[benchmark_column],
        label=benchmark_label,
        color=palette[8],
        linewidth=1.1,
        linestyle="--",
    )

    ax.axhline(0, color=palette[8], linestyle=":", linewidth=0.8)
    ax.set_ylabel("Basis points")
    ax.set_xlabel(None)
    handles, labels = ax.get_legend_handles_labels()
    ax.set_title("USD/CHF Covered Interest Parity Deviations", fontsize=12, pad=5)
    ax.legend(
        handles,
        labels,
        loc="lower left",
        ncol=2,
        frameon=False,
        fontsize=7,
        handlelength=2.0,
        columnspacing=1.0,
        borderaxespad=0.6,
    )
    ax.xaxis.set_major_locator(mdates.YearLocator(4))
    ax.xaxis.set_minor_locator(mdates.YearLocator())
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%Y"))
    plt.setp(ax.get_xticklabels(), rotation=0, ha="center")
    ax.tick_params(axis="x", which="minor", bottom=True)

    note = (
        "Source: Author's calculations using Swiss National Bank and FRED data; benchmark from Du, Keerati & Schreger.\n"
        "Note: D-K-S government-bond CIP is sign-adjusted to match the USD-minus-CHF convention. "
        "Series are annualized basis points."
    )
    fig.text(0.13, 0.095, note, ha="left", va="bottom", fontsize=5.5, color=palette[8], wrap=True)
    plt.subplots_adjust(left=0.13, right=0.97, top=0.90, bottom=0.25)

    os.makedirs(os.path.dirname(CONFIG["CHART_OUTPUT_PATH"]), exist_ok=True)
    plt.savefig(CONFIG["CHART_OUTPUT_PATH"])
    plt.close(fig)

def main() -> None:
    os.makedirs("data", exist_ok=True)
    os.makedirs("chart", exist_ok=True)
    panel, diagnostics, benchmark, validation = build_outputs()
    panel.to_csv(CONFIG["DATA_OUTPUT_PATH"], index_label="Date")
    diagnostics.to_csv(CONFIG["DIAGNOSTICS_OUTPUT_PATH"], index=False)
    benchmark.to_csv(CONFIG["BENCHMARK_OUTPUT_PATH"], index_label="Date")
    validation.to_csv(CONFIG["VALIDATION_OUTPUT_PATH"], index=False)
    plot_outputs(panel)
    print(f"Saved monthly data to {CONFIG['DATA_OUTPUT_PATH']}")
    print(f"Saved diagnostics to {CONFIG['DIAGNOSTICS_OUTPUT_PATH']}")
    print(f"Saved benchmark to {CONFIG['BENCHMARK_OUTPUT_PATH']}")
    print(f"Saved validation to {CONFIG['VALIDATION_OUTPUT_PATH']}")
    print(f"Saved chart to {CONFIG['CHART_OUTPUT_PATH']}")
    print(f"Latest observation: {panel.dropna(how='all').index.max().date()}")


if __name__ == "__main__":
    main()




