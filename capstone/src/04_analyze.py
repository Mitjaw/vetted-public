"""Step 4 - the analysis. Reads data/private/calls_clean.csv + the price cache and writes
results/results.json, the single source for every number and chart on the site.

Design choices (reasons in JOURNAL.md):
  * metric      excess return = stock return - SPY return over the same dates
  * horizons    30 / 90 / 180 / 365 calendar days after the first close following the video
  * uncertainty standard errors clustered two ways, by ticker and by calendar month: repeat
                calls on one stock and calls made in the same month are not independent
  * robustness  QQQ benchmark, beta adjustment, first-calls only, channel-balanced,
                month-balanced, in-depth calls only
  * timing      shift placebo: the same calls moved k trading days earlier/later
  * channels    two-way clustered CIs, Holm correction, label-permutation test, split-half
                persistence
"""
import glob
import json
import numpy as np
import pandas as pd
from scipy import stats
from common import PRIVATE, RESULTS, AUDIT

rng = np.random.default_rng(20261116)
B = 5000
H = [30, 90, 180, 365]
MAIN = 90
MIN_CHANNEL_CALLS = 50

c = pd.read_csv(PRIVATE / "calls_clean.csv", parse_dates=["upload_date", "entry_date", "delisted_on"])
c["month"] = c["upload_date"].dt.to_period("M").astype(str)
c["week"] = c["upload_date"].dt.strftime("%G-%V")
buy, bear = c[c.direction == "buy"].copy(), c[c.direction == "bearish"].copy()
px = pd.read_pickle(PRIVATE / "prices.pkl")
wide = px.pivot(index="date", columns="ticker", values="close").sort_index()
wide = wide[wide["SPY"].notna()]
cal = wide.index
pct = lambda x: round(float(x) * 100, 2)


# ---------------------------------------------------------------- inference helpers ------
def _cluster_meat(X, e, codes):
    """Sum over clusters of (X_g' e_g)(X_g' e_g)' with the usual G/(G-1) correction."""
    G = codes.max() + 1
    S = np.zeros((G, X.shape[1]))
    np.add.at(S, codes, X * e[:, None])
    return S.T @ S * (G / (G - 1)), G


def twoway(y, X, a, b):
    """OLS with two-way cluster-robust covariance (Cameron/Gelbach/Miller):
    V = V_a + V_b - V_(a x b). Returns coefficients, standard errors and the t degrees of
    freedom (smallest cluster count - 1)."""
    ca, cb = pd.factorize(a)[0], pd.factorize(b)[0]
    cab = pd.factorize(pd.Series(ca).astype(str) + "|" + pd.Series(cb).astype(str))[0]
    XtX_inv = np.linalg.inv(X.T @ X)
    beta = XtX_inv @ X.T @ y
    e = y - X @ beta
    Ma, Ga = _cluster_meat(X, e, ca)
    Mb, Gb = _cluster_meat(X, e, cb)
    Mab, _ = _cluster_meat(X, e, cab)
    V = XtX_inv @ (Ma + Mb - Mab) @ XtX_inv
    se = np.sqrt(np.clip(np.diag(V), 1e-18, None))
    return beta, se, min(Ga, Gb) - 1


def _pack(est, se, dof):
    half = stats.t.ppf(0.975, dof) * se
    p = 2 * stats.t.sf(abs(est / se), dof)
    return {"mean": pct(est), "lo": pct(est - half), "hi": pct(est + half), "se": pct(se),
            "p": round(float(p), 4)}


def boot(df, col, cluster="month", n=None):
    """Mean of `col` with a 95% CI clustered by ticker AND by `cluster` (calendar month).
    (Name kept from the first draft, which used a month-block bootstrap - see JOURNAL.)"""
    d = df[[col, "ticker", cluster]].dropna()
    y = d[col].to_numpy(float)
    beta, se, dof = twoway(y, np.ones((len(y), 1)), d["ticker"].to_numpy(), d[cluster].to_numpy())
    return {"n": int(len(d)), **_pack(beta[0], se[0], dof)}


def boot_diff(a, b, col, cluster="month", n=None):
    """Difference of means a - b from one pooled regression, same two-way clustering."""
    d = pd.concat([a.assign(_g=1.0), b.assign(_g=0.0)])[[col, "_g", "ticker", cluster]].dropna()
    X = np.column_stack([np.ones(len(d)), d["_g"].to_numpy()])
    beta, se, dof = twoway(d[col].to_numpy(float), X, d["ticker"].to_numpy(), d[cluster].to_numpy())
    return _pack(beta[1], se[1], dof)


def se_variants(df, col):
    """How much the standard error grows once dependence is respected (shown in Methods)."""
    d = df[[col, "ticker", "month"]].dropna()
    y = d[col].to_numpy(float); X = np.ones((len(y), 1)); e = y - y.mean()
    out = {"iid": pct(y.std(ddof=1) / np.sqrt(len(y)))}
    for name, key in (("month", "month"), ("ticker", "ticker")):
        M, G = _cluster_meat(X, e, pd.factorize(d[key])[0])
        out[name] = pct(np.sqrt(M[0, 0]) / len(y))
    out["twoway"] = pct(twoway(y, X, d["ticker"].to_numpy(), d["month"].to_numpy())[1][0])
    return out


def describe(df, h, bench="ex"):
    col = f"{bench}_{h}"
    d = df.dropna(subset=[col]).assign(win=lambda x: (x[col] > 0).astype(float))
    out = boot(d, col)
    out.update(median=pct(d[col].median()), raw=pct(d[f"ret_{h}"].mean()),
               bench=pct(d[f"{'spy' if bench == 'ex' else 'qqq'}_{h}"].mean()))
    w = boot(d, "win")
    out["hit"], out["hit_lo"], out["hit_hi"] = w["mean"], w["lo"], w["hi"]
    return out


R = {"meta": {"main_horizon": MAIN, "horizons": H,
              "min_channel_calls": MIN_CHANNEL_CALLS, "prices_through": str(cal[-1].date())}}

# ------------------------------------------------------------------- 1. headline ---------
R["headline"] = {str(h): {"spy": describe(buy, h, "ex"), "qqq": describe(buy, h, "exq")} for h in H}

# beta adjustment: market-model beta from the 250 trading days before entry
ret_d = wide.pct_change(fill_method=None)
irx = wide["^IRX"].ffill() / 100 if "^IRX" in wide else pd.Series(0.045, index=cal)
pos = {d: i for i, d in enumerate(cal)}
betas = []
for r in buy.itertuples():
    i = pos[r.entry_date]
    win = ret_d.iloc[max(0, i - 250):i][[r.ticker, "SPY"]].dropna()
    if len(win) >= 120:
        cov = np.cov(win[r.ticker], win["SPY"])
        betas.append(float(np.clip(cov[0, 1] / cov[1, 1], 0, 4)))
    else:
        betas.append(np.nan)
buy["beta"] = betas
for h in H:
    end = buy["entry_date"] + pd.Timedelta(days=h)
    rf = np.array([irx.loc[a:b].mean() * h / 365 if b <= cal[-1] else np.nan
                   for a, b in zip(buy["entry_date"], end)])
    buy[f"alpha_{h}"] = (buy[f"ret_{h}"] - rf) - buy["beta"] * (buy[f"spy_{h}"] - rf)
R["beta"] = {"median": round(float(buy.beta.median()), 2), "mean": round(float(buy.beta.mean()), 2),
             "share_above_1": pct((buy.beta > 1).mean()), "n": int(buy.beta.notna().sum())}

# ------------------------------------------------------------------- 2. robustness -------
first = buy[~buy.is_repeat_90d]
deep = buy[buy.in_depth]
post = buy[buy.upload_date >= "2025-08-01"]      # after the extraction model's training data ends
rob = {}
for h in H:
    col = f"ex_{h}"
    per_ch = buy.dropna(subset=[col]).groupby("channel")[col].agg(["mean", "size"])
    ch_means = per_ch.loc[per_ch["size"] >= MIN_CHANNEL_CALLS, "mean"]     # same channels as the channel chart
    mo = buy.dropna(subset=[col]).groupby("month")[col].mean()
    t = stats.ttest_1samp(mo, 0)
    half = stats.t.ppf(0.975, len(mo) - 1) * mo.std(ddof=1) / np.sqrt(len(mo))
    rob[str(h)] = {
        "all": boot(buy, col),
        "vs_qqq": boot(buy, f"exq_{h}"),
        "beta_adjusted": boot(buy, f"alpha_{h}"),
        "first_calls": boot(first, col),
        "wo_top3": boot(buy[~buy.ticker.isin(buy.groupby("ticker")[col].sum().nlargest(3).index)], col),
        "top3": list(buy.groupby("ticker")[col].sum().nlargest(3).index),
        "in_depth": boot(deep, col),
        "channel_balanced": {"mean": pct(ch_means.mean()), "n": int(len(ch_means))},
        "month_balanced": {"mean": pct(mo.mean()), "lo": pct(mo.mean() - half), "hi": pct(mo.mean() + half),
                           "p": round(float(t.pvalue), 4), "n": int(len(mo)),
                           "months_positive": int((mo > 0).sum())},
        "post_training_cutoff": boot(post, col) if post[col].notna().sum() > 100 else None,
        "pre_training_cutoff": boot(buy[buy.upload_date < "2025-08-01"], col),
    }
R["robustness"] = rob
R["se_variants"] = {str(h): se_variants(buy, f"ex_{h}") for h in H}

# ------------------------------------------------------------------- 3. buy vs bearish ---
R["direction"] = {str(h): {"buy": boot(buy, f"ex_{h}"), "bearish": boot(bear, f"ex_{h}"),
                           "diff": boot_diff(buy, bear, f"ex_{h}"),
                           "diff_in_depth": boot_diff(buy[buy.in_depth], bear[bear.in_depth], f"ex_{h}"),
                           "n_bear_in_depth": int(bear[bear.in_depth][f"ex_{h}"].notna().sum())}
                  for h in H}

# ------------------------------------------------------------------- 4. timing placebo ---
# Move every call by the same k trading days (circular inside the sample window) and
# recompute the mean excess return. If the publication date carries information, the real
# calendar (k = 0) should sit in the upper tail of the shifted ones.
t0, t1 = pos[buy.entry_date.min()], pos[buy.entry_date.max()]
span = t1 - t0 + 1
W = wide.to_numpy()
col_of = {t: j for j, t in enumerate(wide.columns)}
spy = W[:, col_of["SPY"]]
calls_i = buy["entry_date"].map(pos).to_numpy()
calls_j = buy["ticker"].map(col_of).to_numpy()
placebo = {}
for h in [30, 90, 180]:
    end_idx = np.searchsorted(cal.values, (cal + pd.Timedelta(days=h)).values, side="right") - 1
    ok_end = (cal + pd.Timedelta(days=h)) <= cal[-1]

    def mean_at(shift):
        i = t0 + (calls_i - t0 + shift) % span
        e = end_idx[i]
        good = ok_end[i]
        ex = W[e, calls_j] / W[i, calls_j] - spy[e] / spy[i]
        ex = ex[good & np.isfinite(ex)]
        return ex.mean(), len(ex)

    actual, n_act = mean_at(0)
    shifts = np.arange(10, span - 10)
    sims = np.array([mean_at(int(k))[0] for k in shifts])
    hist, edges = np.histogram(sims * 100, bins=30)
    placebo[str(h)] = {"actual": pct(actual), "placebo_mean": pct(sims.mean()),
                       "placebo_lo": pct(np.percentile(sims, 2.5)), "placebo_hi": pct(np.percentile(sims, 97.5)),
                       "percentile": round(float((sims < actual).mean() * 100), 1),
                       "n_shifts": int(len(shifts)), "n_calls": int(n_act),
                       "hist": hist.tolist(), "edges": [round(float(x), 2) for x in edges]}
R["placebo"] = placebo

# ------------------------------------------------------------------- 5. channels ---------
col = f"ex_{MAIN}"
n_by = buy.dropna(subset=[col]).groupby("channel").size()
eligible = sorted(n_by[n_by >= MIN_CHANNEL_CALLS].index)
cb = buy[buy.channel.isin(eligible)].dropna(subset=[col])
split = cb["upload_date"].sort_values().iloc[len(cb) // 2]
rows = []
for ch in eligible:
    d = cb[cb.channel == ch]
    b = boot(d, col)
    top = d["ticker"].value_counts()
    h1, h2 = d[d.upload_date < split][col], d[d.upload_date >= split][col]
    rows.append({"channel": ch, **b, "median": pct(d[col].median()), "hit": pct((d[col] > 0).mean()),
                 "tickers": int(d.ticker.nunique()), "videos": int(d.video_id.nunique()),
                 "top_ticker": top.index[0], "top_share": pct(top.iloc[0] / len(d)),
                 "mean_wo_top": pct(d[d.ticker != top.index[0]][col].mean()),
                 "h1": pct(h1.mean()) if len(h1) >= 20 else None, "n_h1": int(len(h1)),
                 "h2": pct(h2.mean()) if len(h2) >= 20 else None, "n_h2": int(len(h2)),
                 "beta": round(float(buy[buy.channel == ch].beta.median()), 2),
                 "alpha": boot(buy[buy.channel == ch], f"alpha_{MAIN}")})
# Holm step-down correction across the channels
order = np.argsort([r["p"] for r in rows])
m, running = len(rows), 0
for rank, i in enumerate(order):
    running = max(running, min(1, rows[i]["p"] * (m - rank)))
    rows[i]["p_holm"] = round(float(running), 4)
rows.sort(key=lambda r: -r["mean"])

have = [r for r in rows if r["h1"] is not None and r["h2"] is not None]
rho, p_rho = stats.spearmanr([r["h1"] for r in have], [r["h2"] for r in have])
R["channels"] = {"rows": rows, "split_date": str(split.date()),
                 "persistence": {"rho": round(float(rho), 2), "p": round(float(p_rho), 3), "n": len(have)},
                 "n_significant_raw": int(sum(r["p"] < 0.05 for r in rows)),
                 "n_significant_holm": int(sum(r["p_holm"] < 0.05 for r in rows)),
                 "expected_false_positives": round(0.05 * len(rows), 2)}

# ------------------------------------------------------------------- 6. concentration ----
d = buy.dropna(subset=[col])
tk = d.groupby("ticker")[col].agg(["size", "mean"]).sort_values("size", ascending=False)
top10 = tk.head(10)
contrib = d.groupby("ticker")[col].sum().sort_values(ascending=False)
R["concentration"] = {
    "top10": [{"ticker": t, "n": int(r["size"]), "share": pct(r["size"] / len(d)), "mean": pct(r["mean"])}
              for t, r in top10.iterrows()],
    "top10_share": pct(top10["size"].sum() / len(d)),
    "tickers": int(d.ticker.nunique()),
    "single_call_tickers": int((tk["size"] == 1).sum()),
    "mean_all": pct(d[col].mean()),
    "mean_wo_top_contributor": pct(d[d.ticker != contrib.index[0]][col].mean()),
    "top_contributor": contrib.index[0],
    "mean_wo_top3_contributors": pct(d[~d.ticker.isin(contrib.index[:3])][col].mean()),
    "top3_contributors": list(contrib.index[:3]),
    "wo_top3": boot(d[~d.ticker.isin(contrib.index[:3])], col),
    "skew": round(float(stats.skew(d[col])), 2),
    "share_gt_50": pct((d[col] > 0.5).mean()), "share_lt_m50": pct((d[col] < -0.5).mean()),
}

# ------------------------------------------------------------------- 7. cohorts ----------
buy["quarter"] = buy["upload_date"].dt.to_period("Q").astype(str)
R["cohorts"] = [{"quarter": q, **boot(g, col, cluster="week"),
                 "spy": pct(g[f"spy_{MAIN}"].mean()), "raw": pct(g[f"ret_{MAIN}"].mean())}
                for q, g in buy.dropna(subset=[col]).groupby("quarter")]

# ------------------------------------------------------------------- 8. chart data -------
# (a) distribution of excess returns per horizon (hero background + figure 1)
edges = np.arange(-100, 155, 5)
R["distribution"] = {}
for h in H:
    v = buy[f"ex_{h}"].dropna()
    R["distribution"][str(h)] = {
        "edges": edges.tolist(), "counts": np.histogram(np.clip(v * 100, -100, 149.9), bins=edges)[0].tolist(),
        "n": int(len(v)), "mean": pct(v.mean()), "median": pct(v.median()), "hit": pct((v > 0).mean()),
        "p10": pct(v.quantile(.1)), "p90": pct(v.quantile(.9)), "clipped_above": int((v * 100 >= 150).sum())}

# (b) event-time path: mean cumulative excess return for trading days 0..250 after entry,
#     for the calls whose full 365-day window is observable (constant sample along the line)
def path(df, days=250):
    df = df[df["ex_365"].notna()]
    i, j = df["entry_date"].map(pos).to_numpy(), df["ticker"].map(col_of).to_numpy()
    Wf = pd.DataFrame(W).ffill().to_numpy()                      # delisted: hold last price
    k = np.arange(days + 1)
    stock = Wf[i[:, None] + k, j[:, None]] / Wf[i, j][:, None]
    bench = spy[i[:, None] + k] / spy[i][:, None]
    ex = stock - bench
    ct, cm = pd.factorize(df["ticker"])[0], pd.factorize(df["month"])[0]
    cx = pd.factorize(pd.Series(ct).astype(str) + "|" + pd.Series(cm).astype(str))[0]
    e = ex - ex.mean(0)

    def var(codes):                                    # cluster variance of the mean, per day
        G = codes.max() + 1
        S = np.zeros((G, e.shape[1])); np.add.at(S, codes, e)
        return (S ** 2).sum(0) * G / (G - 1) / len(e) ** 2

    se = np.sqrt(np.clip(var(ct) + var(cm) - var(cx), 0, None))
    half = stats.t.ppf(0.975, cm.max()) * se
    lo, hi = ex.mean(0) - half, ex.mean(0) + half
    r1 = lambda a: [round(float(v) * 100, 2) for v in a]
    return {"n": int(len(df)), "mean": r1(ex.mean(0)), "lo": r1(lo), "hi": r1(hi)}

R["path"] = {"buy": path(buy), "bearish": path(bear)}

# ------------------------------------------------------------------- 9. data quality -----
a = pd.read_csv(AUDIT / "extraction_audit.csv").merge(
    pd.read_csv(PRIVATE / "mentions_raw.csv")[["mention_id", "mention_count"]], on="mention_id")
a["genuine"] = a["label"].isin(["A", "B"])


def wilson(k, n):
    lo, hi = stats.binomtest(int(k), int(n)).proportion_ci(method="wilson")
    return {"k": int(k), "n": int(n), "share": pct(k / n), "lo": pct(lo), "hi": pct(hi)}


ab, ae = a[a.direction == "buy"], a[a.direction == "bearish"]
R["audit"] = {
    "buy_genuine": wilson(ab.genuine.sum(), len(ab)),
    "buy_explicit": wilson((ab.label == "A").sum(), len(ab)),
    "buy_relayed": wilson((ab.label == "T").sum(), len(ab)),
    "buy_not_a_call": wilson((ab.label == "C").sum(), len(ab)),
    "buy_in_depth_genuine": wilson(ab[ab.mention_count >= 4].genuine.sum(), (ab.mention_count >= 4).sum()),
    "buy_other_genuine": wilson(ab[ab.mention_count < 4].genuine.sum(), (ab.mention_count < 4).sum()),
    "bearish_genuine": wilson(ae.genuine.sum(), len(ae)),
    "bearish_not_a_call": wilson((ae.label == "C").sum(), len(ae)),
    "bearish_explicit": wilson((ae.label == "A").sum(), len(ae)),
    "bearish_relayed": wilson((ae.label == "T").sum(), len(ae)),
    "ticker_correct": wilson(a.ticker_ok.sum(), len(a)),
    "wrong_ticker_surviving_cleaning": int((a[a.ticker_ok == 0]["mention_id"].isin(c["mention_id"])).sum()),
}
# the project's own extraction benchmark (hand-annotated videos, repeated runs of the production set-up)
runs = []
for f in glob.glob("/root/vetted/evals/results/*three_pass_haiku.json"):
    for r in json.load(open(f)).get("three_pass_haiku", []):
        mt = r.get("metrics") or {}
        if "precision" in mt:
            runs.append((r["video_id"], mt["precision"], mt["recall"], mt.get("rec_acc")))
if runs:
    e = pd.DataFrame(runs, columns=["video", "precision", "recall", "label_acc"])
    R["benchmark"] = {"runs": int(len(e)), "videos": int(e.video.nunique()),
                      "precision": pct(e.precision.mean()), "recall": pct(e.recall.mean()),
                      "label_acc": pct(e.label_acc.mean()),
                      "recall_min": pct(e.recall.min()), "recall_max": pct(e.recall.max())}

R["cleaning"] = json.load(open(RESULTS / "cleaning_log.json"))
R["dataset"] = {
    "calls": int(len(c)), "buy": int(len(buy)), "bearish": int(len(bear)),
    "channels": int(c.channel.nunique()), "videos": int(c.video_id.nunique()), "tickers": int(c.ticker.nunique()),
    "first_video": str(c.upload_date.min().date()), "last_video": str(c.upload_date.max().date()),
    "per_channel": [{"channel": ch, "buy": int((g.direction == "buy").sum()),
                     "bearish": int((g.direction == "bearish").sum()), "videos": int(g.video_id.nunique())}
                    for ch, g in c.groupby("channel")],
    "repeat_share": pct(buy.is_repeat_90d.mean()), "in_depth_share": pct(buy.in_depth.mean()),
}
json.dump(R, open(RESULTS / "results.json", "w"), indent=1)

# ---- console summary --------------------------------------------------------------------
for h in H:
    s = R["headline"][str(h)]["spy"]; q = R["headline"][str(h)]["qqq"]
    print(f"{h:>3}d n={s['n']} raw={s['raw']} spy={s['bench']} ex={s['mean']} [{s['lo']},{s['hi']}] p={s['p']} "
          f"med={s['median']} hit={s['hit']} | vsQQQ {q['mean']} [{q['lo']},{q['hi']}]")
print("beta", R["beta"])
for h in H:
    print(h, {k: (v["mean"], v.get("lo"), v.get("hi")) for k, v in rob[str(h)].items() if isinstance(v, dict)})
print("direction", {h: (v["buy"]["mean"], v["bearish"]["mean"], v["diff"]) for h, v in R["direction"].items()})
print("placebo", {h: {k: v[k] for k in ("actual", "placebo_mean", "placebo_lo", "placebo_hi", "percentile")} for h, v in placebo.items()})
for r in rows:
    print(f"{r['channel'][:28]:<28} n={r['n']:<5} ex={r['mean']:>6} [{r['lo']},{r['hi']}] p={r['p']} holm={r['p_holm']} "
          f"top={r['top_ticker']} {r['top_share']}% wo_top={r['mean_wo_top']} h1={r['h1']} h2={r['h2']} beta={r['beta']} alpha={r['alpha']['mean']} [{r['alpha']['lo']},{r['alpha']['hi']}]")
print("SE variants", R["se_variants"])
print({k: v for k, v in R["channels"].items() if k != "rows"})
print(R["concentration"])
print([(q["quarter"], q["n"], q["mean"], q["lo"], q["hi"]) for q in R["cohorts"]])
print(R["audit"]); print(R.get("benchmark"))
