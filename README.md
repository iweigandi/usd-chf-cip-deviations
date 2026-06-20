# Monthly USD/CHF Covered Interest Parity Deviations

![Update USD/CHF CIP Data and Chart](https://github.com/iweigandi/usd-chf-cip-deviations/actions/workflows/update_cip.yml/badge.svg)

This repository provides monthly estimates of covered interest parity (CIP) deviations for the USD/CHF pair. The measures are constructed from spot and forward exchange rates together with short-term USD and CHF interest-rate benchmarks. The series distinguish between risk-free-rate measures based on SOFR and SARON, legacy LIBOR-based measures, and government money-market rate proxies.

The dataset also includes a CHF three-month government-bond CIP benchmark from Du, Keerati, and Schreger. The benchmark is sign-adjusted so that it follows the same USD-minus-CHF convention as the series constructed here. Because the benchmark is based on government-bond yields rather than the short-rate proxies used in this repository, it should be interpreted as an external comparison rather than a mechanically identical target series.

---

### Chart

![USD/CHF CIP deviations](chart/usd_chf_cip_deviations.png)

---

### Data

The generated data can be accessed directly:

* **Monthly CIP deviations and source series:** [`data/usd_chf_cip_deviations_monthly.csv`](data/usd_chf_cip_deviations_monthly.csv)
* **Du-Keerati-Schreger CHF 3M benchmark:** [`data/du_keerati_schreger_chf_cip_monthly.csv`](data/du_keerati_schreger_chf_cip_monthly.csv)
* **Benchmark correlations:** [`data/benchmark_validation.csv`](data/benchmark_validation.csv)
* **Source diagnostics:** [`data/source_diagnostics.csv`](data/source_diagnostics.csv)

---

### Methodology

The data use CHF per USD spot and forward exchange rates. For tenor \(T\), the forward-implied USD-CHF interest-rate differential is defined as:

```text
- log(F_t^T / S_t) / T
```

where \(S_t\) is the spot exchange rate and \(F_t^T\) is the forward rate for maturity \(T\). The CIP deviation is then:

```text
(r_USD,t^T - r_CHF,t^T) - [-log(F_t^T / S_t) / T]
```

The output series are annualized and reported in basis points. The repository currently reports:

* `cip_basis_sofr_saron_3m_bps`
* `cip_basis_sofr_saron_6m_bps`
* `cip_basis_libor_3m_bps`
* `cip_basis_government_3m_bps`
* `dks_chf_govt_cip_3m_bps`

---

### Sources

* Swiss National Bank: USD/CHF spot and forward rates; CHF SARON compound rates; CHF money-market and legacy LIBOR rates.
* FRED: SOFR index and US Treasury bill rate.
* Du, Keerati, and Schreger: government-bond CIP dataset, version 4.

---

### Replication

Run:

```bash
pip install -r requirements.txt
python usd_chf_cip.py
```

The script downloads public data, computes the monthly series, writes diagnostics and benchmark correlations, and regenerates the chart. A GitHub Action can run the same pipeline on a schedule.

For a fuller explanation of the data and definitions, see the [Methodological Note](Methodology.pdf).