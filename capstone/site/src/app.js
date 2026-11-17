/* Vetted case study. Every number and chart is rendered from DATA (results/results.json). */
(() => {
const D = DATA, $ = (s, r = document) => r.querySelector(s), $$ = (s, r = document) => [...r.querySelectorAll(s)];
const NS = "http://www.w3.org/2000/svg";
const C = { ink: "#1b1b1b", ink3: "#6f6f6b", line: "#deded8", soft: "#f1f1ec", data: "#1F5FBF", data2: "#8c8c86", bg: "#FFFFFF" };

/* ---------- formatting ---------- */
const sgn = x => (x > 0 ? "+" : x < 0 ? "−" : "");
const F = {
  int: x => Math.round(x).toLocaleString("en-US"),
  pp: x => `${sgn(x)}${Math.abs(x).toFixed(1)} pp`,
  rel: x => `${Math.abs(x).toFixed(1)} points ${x < 0 ? "behind" : "ahead of"} the index`.replace("ahead of the index", "ahead of the index"),
  abs1: x => Math.abs(x).toFixed(1),
  pct: x => `${x.toFixed(1)}%`,
  pct0: x => `${Math.round(x)}%`,
  ord: x => { const n = Math.round(x), s = ["th", "st", "nd", "rd"], v = n % 100; return n + (s[(v - 20) % 10] || s[v] || s[0]); },
  "x-iid90": x => `${(x / D.se_variants["90"].iid).toFixed(1)} times`,
  "x-iid365": x => `${(x / D.se_variants["365"].iid).toFixed(1)} times`,
};
const get = p => p.split(".").reduce((o, k) => (o == null ? o : o[k]), D);
$$("[data-v]").forEach(n => { const v = get(n.dataset.v), f = n.dataset.f; n.textContent = v == null ? "n/a" : f ? F[f](v) : v; });

/* ---------- svg helpers ---------- */
const el = (tag, attrs = {}, parent) => { const n = document.createElementNS(NS, tag); for (const k in attrs) n.setAttribute(k, attrs[k]); if (parent) parent.appendChild(n); return n; };
const svgIn = (host, w, h, label) => { host.innerHTML = ""; const s = el("svg", { viewBox: `0 0 ${w} ${h}`, role: "img" }, host); if (label) s.setAttribute("aria-label", label); return s; };
const lin = (d0, d1, r0, r1) => x => r0 + (x - d0) / (d1 - d0) * (r1 - r0);
const widthOf = host => Math.max(260, Math.round(host.getBoundingClientRect().width));
const ticks = (lo, hi, n = 6) => { const span = hi - lo, mag = Math.pow(10, Math.floor(Math.log10(span / n))); const step = [1, 2, 2.5, 5, 10].map(m => m * mag).find(s => span / s <= n) || 10 * mag; const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6)); return out; };
const num = v => (v > 0 ? "+" : v < 0 ? "−" : "") + Math.abs(v);

/* ---------- tooltip: hover with a mouse, tap on a touch screen, focus with the keyboard ---------- */
const tip = $("#tip");
const place = (x, y) => { const r = tip.getBoundingClientRect(); let px = x + 14, py = y + 16; if (px + r.width > innerWidth - 8) px = Math.max(8, x - r.width - 14); if (py + r.height > innerHeight - 8) py = Math.max(8, y - r.height - 16); tip.style.left = px + "px"; tip.style.top = py + "px"; };
const showTip = (html, x, y) => { tip.innerHTML = html; tip.classList.add("show"); place(x, y); };
const hideTip = () => { tip.classList.remove("show"); $$(".dotrow.on").forEach(n => n.classList.remove("on")); };
const withTip = (node, html) => {
  const h = () => (typeof html === "function" ? html() : html);
  node.addEventListener("pointerenter", e => { if (e.pointerType === "mouse") showTip(h(), e.clientX, e.clientY); });
  node.addEventListener("pointermove", e => { if (e.pointerType === "mouse") place(e.clientX, e.clientY); });
  node.addEventListener("pointerleave", e => { if (e.pointerType === "mouse") hideTip(); });
  node.addEventListener("click", e => { e.stopPropagation(); hideTip(); showTip(h(), e.clientX, e.clientY); if (node.classList.contains("dotrow")) node.classList.add("on"); });
  node.addEventListener("focus", () => { const r = node.getBoundingClientRect(); showTip(h(), r.left + 20, r.top + r.height / 2); });
  node.addEventListener("blur", hideTip);
};
document.addEventListener("click", hideTip);
addEventListener("scroll", hideTip, { passive: true });

/* ---------- tab controls ---------- */
const state = { dist: "90", lens: "180", ch: "spy" };
const tabs = { dist: D.meta.horizons.map(h => [String(h), h + " days"]), lens: D.meta.horizons.map(h => [String(h), h + " days"]), ch: [["spy", "vs S&P 500"], ["alpha", "adjusted for risk"]] };
const draw = {};
$$("[data-seg]").forEach(host => {
  const key = host.dataset.seg;
  tabs[key].forEach(([val, label]) => {
    const b = document.createElement("button"); b.type = "button"; b.textContent = label; b.setAttribute("aria-pressed", String(state[key] === val));
    b.addEventListener("click", () => { state[key] = val; $$("button", host).forEach(x => x.setAttribute("aria-pressed", String(x === b))); draw[key](); });
    host.appendChild(b);
  });
});

/* =====================================================================================
   TOP: background chart and the six-step navigation
   ===================================================================================== */
function hero() {
  // Small version of the Finding 1 chart: buy calls by result after 90 days, zoomed to -50..+100 points.
  const host = $("#heroChart"), d = D.distribution["90"];
  const i0 = d.edges.indexOf(-50), i1 = d.edges.indexOf(100), counts = d.counts.slice(i0, i1), edges = d.edges.slice(i0, i1 + 1);
  const render = () => {
    const w = widthOf(host), H = Math.round(Math.min(230, Math.max(170, w * 0.6))), m = { l: 1, r: 1, t: 44, b: 22 }, iw = w - m.l - m.r, ih = H - m.t - m.b;
    const max = Math.max(...counts), bw = iw / counts.length, y = lin(0, max, 0, ih), zi = edges.indexOf(0), x0 = m.l + zi * bw;
    const svg = svgIn(host, w, H, `Of ${F.int(d.n)} buy calls, ${F.pct0(100 - d.hit)} did worse than the S&P 500 after 90 days and ${F.pct0(d.hit)} did better`);
    counts.forEach((c, i) => el("rect", { x: m.l + i * bw + 0.75, y: m.t + ih - y(c), width: Math.max(1, bw - 1.5), height: y(c), fill: edges[i] >= 0 ? C.data : C.data2 }, svg));
    el("line", { x1: m.l, x2: m.l + iw, y1: m.t + ih, y2: m.t + ih, stroke: C.ink }, svg);
    el("line", { x1: x0, x2: x0, y1: 4, y2: m.t + ih + 5, stroke: C.ink }, svg);
    const lab = (txt, sub, anchor, dx) => { const a = el("text", { x: x0 + dx, y: 17, "text-anchor": anchor, class: "strong big" }, svg); a.textContent = txt; const s = el("text", { x: x0 + dx, y: 33, "text-anchor": anchor }, svg); s.textContent = sub; };
    lab(F.pct0(100 - d.hit), "did worse", "end", -8); lab(F.pct0(d.hit), "did better", "start", 8);
    const z = el("text", { x: x0, y: H - 4, "text-anchor": "middle" }, svg); z.textContent = "same as the S&P 500";
  };
  render(); new ResizeObserver(render).observe(host);
  $("#heroCap").textContent = `${F.int(d.n)} buy calls, 90 days after the video. Each bar counts calls by how far they ended behind or ahead of the S&P 500. The few calls beyond −50 or +100 points are not drawn.`;
}
function rail() {
  const texts = ["What I wanted to know, and who the answer is for.",
    "Where the data comes from and how far it can be trusted.",
    `Cleaning: from ${F.int(D.cleaning.raw.mentions)} raw mentions to ${F.int(D.dataset.calls)} usable calls.`,
    "Four findings, each with a chart and a note on how sure it is.",
    "The dataset, the notebook, the code and the data journal.",
    "What viewers and people who build rankings can take from this."];
  const idle = "Point at a step to see what it covers. Click to jump there.";
  const track = $("#railTrack"), links = $$("a", track), text = $("#railText");
  const set = i => { links.forEach((a, k) => a.classList.toggle("on", k === i)); text.textContent = i < 0 ? idle : texts[i]; };
  links.forEach((a, i) => {
    a.addEventListener("pointerenter", () => set(i)); a.addEventListener("focus", () => set(i));
    a.addEventListener("pointerdown", () => set(i));
  });
  track.addEventListener("pointerleave", e => { if (e.pointerType === "mouse") set(-1); });
  track.addEventListener("focusout", e => { if (!track.contains(e.relatedTarget)) set(-1); });
  track.addEventListener("keydown", e => { if (e.key === "ArrowRight" || e.key === "ArrowLeft") { e.preventDefault(); const cur = links.indexOf(document.activeElement); links[(cur + (e.key === "ArrowRight" ? 1 : 5)) % 6].focus(); } });
  set(-1);
}

/* =====================================================================================
   PREPARE and PROCESS
   ===================================================================================== */
function sampleRows() {
  const t = $("#sampleRows"), R = D.sample_rows || [];
  t.innerHTML = `<thead><tr><th>Video date</th><th>Channel</th><th>Ticker</th><th>Direction</th><th>Model’s label</th><th>Times named</th><th>Repeat</th></tr></thead><tbody>` +
    R.map(r => `<tr><td>${r.video_date}</td><td>${r.channel}</td><td>${r.ticker}</td><td>${r.direction}</td><td>${r.llm_label}</td><td>${r.mention_count}</td><td>${r.is_repeat_90d === "True" ? "yes" : "no"}</td></tr>`).join("") + "</tbody>";
}
function audit() {
  const A = D.audit;
  const mk = (host, n, parts) => parts.forEach(([k, col, lab, light]) => { if (!k) return; const i = document.createElement("i"); i.style.background = col; i.style.width = k / n * 100 + "%"; if (light) i.className = "light"; if (k / n > 0.07) i.textContent = k; i.tabIndex = 0; withTip(i, `<b>${k} of ${n}</b> (${Math.round(k / n * 100)}%)<small>${lab}</small>`); host.appendChild(i); });
  const b = A.buy_genuine, e = A.bearish_genuine;
  mk($("#stackBuy"), b.n, [[A.buy_explicit.k, C.data, "clear recommendation"], [b.k - A.buy_explicit.k, "#7fa3dc", "clear positive opinion, no explicit advice", 1], [A.buy_relayed.k, "#c9c9c2", "someone else’s pick, passed on", 1], [A.buy_not_a_call.k, C.data2, "no opinion: an example, a news item, a comparison"]]);
  mk($("#stackBear"), e.n, [[A.bearish_explicit.k, C.data, "clear warning"], [e.k - A.bearish_explicit.k, "#7fa3dc", "clear negative opinion, no explicit advice", 1], [A.bearish_relayed.k, "#c9c9c2", "someone else’s view, passed on", 1], [A.bearish_not_a_call.k, C.data2, "no opinion: an example, a news item, a comparison"]]);
}
function funnel() {
  const host = $("#funnel"), why = $("#fwhy"), S = D.cleaning.steps, raw = S[0].before;
  const names = { "directional label": ["Clear buy or warning", "Labels such as “hold”, “watch” or “mentioned” are not something a viewer can act on."], "label agrees with sentiment": ["Label and tone agree", "Rows where the model says “buy” but also says the tone is negative, or the other way round. The model contradicts itself."], "single stocks": ["Single stocks only", "Funds, crypto, commodities and indices are not stock picks. A fund that tracks the S&P 500 can never beat the S&P 500."], "US listing": ["Listed in the US", "Stocks listed elsewhere trade in other currencies. Comparing them with a US index would mix in exchange rates."], "price series found": ["Ticker has prices", "The ticker does not exist anywhere. Mostly garbled or invented symbols such as NVIDIA or FORTNET."], "core channels": ["Channels with 50+ videos", "Channels with fewer than 50 analysed videos, mostly from a short test in March 2026. Too little to say anything about them."], "one call per channel/ticker/day": ["One call per channel, stock and day", "Two videos by one channel on the same day repeating the same call."], "entry price available": ["Could be bought the next day", "No price in the days after the video: the company was not listed yet or had already disappeared."], "entry price at least $1": ["Share price of $1 or more", "Shares of bankrupt companies quoted at fractions of a cent jump by thousands of percent from tiny price moves. One such row had distorted an average."] };
  const rows = [{ lab: "Everything the model extracted", n: raw, cut: 0, why: `Every stock, fund, coin or commodity the model found in ${F.int(D.cleaning.raw.videos_with_mentions)} videos.` }]
    .concat(S.filter(s => s.removed > 0).map(s => ({ lab: (names[s.step] || [s.step])[0], n: s.after, cut: s.removed, why: (names[s.step] || [0, s.why])[1] })));
  rows.forEach((r, i) => {
    const row = document.createElement("button"); row.type = "button"; row.className = "frow" + (i === rows.length - 1 ? " final" : "");
    row.innerHTML = `<span class="lab">${r.lab}${r.cut ? `<small>−${F.int(r.cut)} rows</small>` : ""}</span><span class="track"><span class="fill" style="width:${r.n / raw * 100}%"></span><span class="cut" style="left:${r.n / raw * 100}%;width:${r.cut / raw * 100}%"></span></span><span class="n">${F.int(r.n)}</span>`;
    const on = () => { $$(".frow", host).forEach(x => x.classList.remove("on")); row.classList.add("on"); why.innerHTML = `<b>${r.lab}.</b> ${r.why}`; };
    row.addEventListener("pointerenter", e => { if (e.pointerType === "mouse") on(); }); row.addEventListener("click", on); row.addEventListener("focus", on);
    host.appendChild(row); if (i === 1) on();
  });
}

/* =====================================================================================
   FINDING 1: distribution
   ===================================================================================== */
draw.dist = () => {
  const host = $('[data-chart="dist"]'), h = state.dist, d = D.distribution[h];
  const w = widthOf(host), small = w < 520, H = small ? 250 : 300, m = { l: 4, r: 4, t: 40, b: 46 }, iw = w - m.l - m.r, ih = H - m.t - m.b;
  const gmax = Math.max(...D.meta.horizons.map(k => Math.max(...D.distribution[k].counts.map(c => c / D.distribution[k].n))));
  const x = lin(d.edges[0], d.edges[d.edges.length - 1], m.l, m.l + iw), y = lin(0, gmax, 0, ih), bw = iw / d.counts.length;
  let svg = $("svg", host);
  if (!svg || svg.dataset.cw != w) {
    svg = svgIn(host, w, H, "Histogram of buy calls by result against the S&P 500"); svg.dataset.cw = w;
    el("line", { x1: m.l, x2: m.l + iw, y1: m.t + ih, y2: m.t + ih, stroke: C.ink }, svg);
    for (let v = -100; v <= 150; v += 50) { const t = el("text", { x: x(v), y: m.t + ih + 17, "text-anchor": v === -100 ? "start" : v === 150 ? "end" : "middle" }, svg); t.textContent = num(v) + (v === 150 ? (small ? "+" : " or more") : ""); }
    const a1 = el("text", { x: m.l, y: H - 6 }, svg); a1.textContent = "← worse than the index";
    const a2 = el("text", { x: m.l + iw, y: H - 6, "text-anchor": "end" }, svg); a2.textContent = "better than the index →";
    el("g", { class: "bars" }, svg); el("g", { class: "marks" }, svg); el("g", { class: "hits" }, svg);
  }
  const g = $(".bars", svg), marks = $(".marks", svg), hits = $(".hits", svg);
  d.counts.forEach((c, i) => {
    let r = g.children[i], hit = hits.children[i];
    if (!r) { r = el("rect", { class: "bar-r", x: m.l + i * bw + 0.75, width: Math.max(1, bw - 1.5), y: m.t + ih, height: 0 }, g); hit = el("rect", { x: m.l + i * bw, width: bw, y: m.t, height: ih, fill: "transparent" }, hits); withTip(hit, () => hit._tip); }
    const lo = d.edges[i], last = i === d.counts.length - 1;
    r.setAttribute("fill", lo >= 0 ? C.data : C.data2);
    hit._tip = `<b>${num(lo)} to ${num(lo + 5)}${last ? " points or more" : " points"}</b><small>${F.int(c)} calls, ${(c / d.n * 100).toFixed(1)}% of all</small>`;
    const hh = y(c / d.n); r.style.y = (m.t + ih - hh) + "px"; r.style.height = hh + "px"; r.setAttribute("y", m.t + ih - hh); r.setAttribute("height", hh);
  });
  marks.innerHTML = "";
  const left = d.median <= d.mean;
  const mark = (v, label, dash, anchor, yy) => { el("line", { x1: x(v), x2: x(v), y1: yy + 4, y2: m.t + ih, stroke: C.ink, "stroke-width": 1.5, "stroke-dasharray": dash }, marks); const t = el("text", { x: x(v) + (anchor === "end" ? -5 : 5), y: yy + 12, "text-anchor": anchor, class: "strong" }, marks); t.textContent = label; };
  mark(d.median, small ? "middle call" : `middle call ${F.pp(d.median)}`, "2 3", left ? "end" : "start", 2);
  mark(d.mean, small ? "average" : `average ${F.pp(d.mean)}`, "", left ? "start" : "end", 18);
  $("#distN").textContent = F.int(d.n); $("#dMed").textContent = F.pp(d.median); $("#dHit").textContent = F.pct0(d.hit); $("#dMean").textContent = F.pp(d.mean);
};

/* =====================================================================================
   FINDINGS 2 and 4: dot with range, one row per item
   ===================================================================================== */
function dotRows(host, rows, label) {
  const w = widthOf(host), small = w < 600, labW = small ? 0 : 280, valW = small ? 0 : 72, rh = small ? 56 : 40;
  const m = { l: labW + 6, r: valW + 10, t: 26, b: 6 }, iw = w - m.l - m.r, H = m.t + rows.length * rh + m.b;
  const lo = Math.min(0, ...rows.map(r => r.lo ?? r.mean)), hi = Math.max(0, ...rows.map(r => r.hi ?? r.mean)), pad = (hi - lo) * 0.06;
  const x = lin(lo - pad, hi + pad, m.l, m.l + iw), svg = svgIn(host, w, H, label);
  const tk = ticks(lo - pad, hi + pad, small ? 4 : 7);
  tk.forEach(v => { if (!small) el("line", { x1: x(v), x2: x(v), y1: m.t - 6, y2: H - m.b, stroke: v === 0 ? C.ink : C.line }, svg); const t = el("text", { x: x(v), y: 13, "text-anchor": "middle", class: v === 0 ? "strong" : "" }, svg); t.textContent = num(v); });
  rows.forEach((r, i) => {
    const top = m.t + i * rh, cy = small ? top + 38 : top + rh / 2, g = el("g", { class: "dotrow", tabindex: 0 }, svg);
    el("rect", { class: "hit", x: 0, y: top, width: w, height: rh }, g);
    if (small) tk.forEach(v => el("line", { x1: x(v), x2: x(v), y1: top + 27, y2: top + rh - 5, stroke: v === 0 ? C.ink : C.line }, g));
    const lab = el("text", { x: 4, y: small ? top + 19 : cy + 4, class: "strong" }, g); lab.textContent = r.label; lab.style.fontSize = small ? "14px" : "14.5px";
    const val = el("text", { x: w - 4, y: small ? top + 19 : cy + 4, "text-anchor": "end", class: "strong" }, g); val.textContent = F.pp(r.mean); val.style.fontSize = "14px";
    if (r.lo != null) el("line", { x1: x(r.lo), x2: x(r.hi), y1: cy, y2: cy, stroke: C.data, "stroke-width": 2, opacity: .55 }, g);
    const solid = r.lo != null && (r.lo > 0 || r.hi < 0);
    el("circle", { cx: x(r.mean), cy, r: 5.5, fill: solid ? C.data : C.bg, stroke: C.data, "stroke-width": 2 }, g);
    withTip(g, r.tip);
  });
}
draw.lens = () => {
  const r = D.robustness[state.lens];
  const row = (key, label, note, sub) => r[key] ? { label, ...r[key], tip: `<b>${label}</b><small>${note}</small>${F.pp(r[key].mean)}${r[key].lo != null ? `, range ${F.pp(r[key].lo)} to ${F.pp(r[key].hi)}` : " (no range calculated)"}<small>${sub || `${F.int(r[key].n)} calls`}</small>` } : null;
  const rows = [
    row("all", "All buy calls", "Every call counts once, compared with the S&P 500."),
    row("vs_qqq", "Compared with the Nasdaq-100", "A tech-heavy index for tech-heavy picks."),
    row("beta_adjusted", "Adjusted for risk", "Minus the gain expected from stocks that move more than the market."),
    row("wo_top3", "Without the three best stocks", `Leaves out ${r.top3.join(", ")}.`),
    row("first_calls", "Each idea counted once", "No repeats of the same call by the same channel within 90 days."),
    row("in_depth", "Only stocks discussed at length", "Stock named four or more times in the video."),
    row("month_balanced", "Every month counts equally", `Average of ${r.month_balanced.n} monthly averages.`, `${r.month_balanced.months_positive} of ${r.month_balanced.n} months above zero`),
    row("channel_balanced", "Every channel counts equally", `Average of ${r.channel_balanced.n} channel averages.`, `${r.channel_balanced.n} channels`),
  ].filter(Boolean);
  dotRows($('[data-chart="lens"]'), rows, "Average result of buy calls, calculated in eight ways");
};
draw.ch = () => {
  const mode = state.ch, R = D.channels.rows.slice().sort((a, b) => (mode === "spy" ? b.mean - a.mean : b.alpha.mean - a.alpha.mean));
  const rows = R.map(r => { const s = mode === "spy" ? r : r.alpha; return { label: r.channel.replace(" with Joseph Hogue, CFA", ""), mean: s.mean, lo: s.lo, hi: s.hi,
    tip: `<b>${r.channel}</b><small>${F.int(r.n)} buy calls on ${r.tickers} stocks</small>vs S&P 500: ${F.pp(r.mean)}, range ${F.pp(r.lo)} to ${F.pp(r.hi)}<br>adjusted for risk: ${F.pp(r.alpha.mean)}, range ${F.pp(r.alpha.lo)} to ${F.pp(r.alpha.hi)}<small>most-named stock: ${r.top_ticker}, ${F.pct0(r.top_share)} of its calls · moves ${r.beta.toFixed(1)}× the market</small>` }; });
  dotRows($('[data-chart="ch"]'), rows, "Average 90-day result per channel");
};
function findingText() {
  const r = D.robustness["180"], share = 1 - r.wo_top3.mean / r.all.mean;
  $("#top3").textContent = r.top3.join(", ");
  $("#top3share").textContent = share > 0.5 ? "More than half" : share > 0.33 ? "More than a third" : "Part";
  const Ch = D.channels, R = Ch.rows, top = R[0], words = ["no", "one", "two", "three", "four"];
  $("#chSub").innerHTML = `${top.channel} is in front, with buy calls that were ${F.abs1(top.mean)} points ahead of the index after 90 days. ${F.pct0(top.top_share)} of those calls are on one stock (${top.top_ticker}), and its picks move about ${top.beta.toFixed(1)} times as much as the market. I am comparing ${R.length} channels at once, and with that many a lead of this size can come from luck.`;
  $("#chDetail").innerHTML = `Before allowing for the number of channels, ${words[Ch.n_significant_raw] || Ch.n_significant_raw} of ${R.length} channels differ from zero at the usual 5% level; by luck alone you would expect ${Ch.expected_false_positives}. After the correction for testing ${R.length} channels (Holm), ${words[Ch.n_significant_holm] || Ch.n_significant_holm} do. ${top.channel}: ${F.pp(top.mean)} (95% range ${F.pp(top.lo)} to ${F.pp(top.hi)}, p = ${top.p.toFixed(3)}, corrected ${top.p_holm.toFixed(2)}); adjusted for risk ${F.pp(top.alpha.mean)}. Do good channels stay good? Only ${Ch.persistence.n} channels have enough calls in both halves of the period. Their results in the first and second half are only weakly related (rank correlation ${Ch.persistence.rho}, p = ${Ch.persistence.p}).`;
}

/* =====================================================================================
   FINDING 3: year-long path and alternative calendars
   ===================================================================================== */
draw.path = () => {
  const host = $('[data-chart="path"]'), P = D.path, w = widthOf(host), small = w < 520, H = small ? 280 : 340, m = { l: 34, r: small ? 8 : 132, t: small ? 40 : 12, b: 28 }, iw = w - m.l - m.r, ih = H - m.t - m.b;
  const n = P.buy.mean.length, all = [...P.buy.lo, ...P.buy.hi, ...P.bearish.lo, ...P.bearish.hi], lo = Math.min(...all), hi = Math.max(...all);
  const x = lin(0, n - 1, m.l, m.l + iw), y = lin(lo, hi, m.t + ih, m.t), svg = svgIn(host, w, H, "Average result of recommended and warned-about stocks over one year");
  ticks(lo, hi, 5).forEach(v => { el("line", { x1: m.l, x2: m.l + iw, y1: y(v), y2: y(v), stroke: v === 0 ? C.ink : C.line }, svg); const t = el("text", { x: m.l - 6, y: y(v) + 4, "text-anchor": "end", class: v === 0 ? "strong" : "" }, svg); t.textContent = num(v); });
  [0, 63, 126, 189, 250].forEach((k, i) => { const t = el("text", { x: x(k), y: H - 8, "text-anchor": i === 0 ? "start" : i === 4 ? "end" : "middle" }, svg); t.textContent = i === 0 ? "video" : `${[3, 6, 9, 12][i - 1]} ${small ? "mo" : "months"}`; });
  const series = [["bearish", C.data2, "Warned about"], ["buy", C.data, "Recommended"]];
  const ends = series.map(([k]) => y(P[k].mean[n - 1])); if (Math.abs(ends[0] - ends[1]) < 34) { const mid = (ends[0] + ends[1]) / 2, s = ends[0] < ends[1] ? -1 : 1; ends[0] = mid + s * 17; ends[1] = mid - s * 17; }
  series.forEach(([k, col, name], si) => {
    const s = P[k];
    el("polygon", { points: s.hi.map((v, i) => `${x(i)},${y(v)}`).join(" ") + " " + s.lo.map((v, i) => `${x(i)},${y(v)}`).reverse().join(" "), fill: col, "fill-opacity": .13 }, svg);
    el("path", { d: "M" + s.mean.map((v, i) => `${x(i)},${y(v)}`).join("L"), fill: "none", stroke: col, "stroke-width": 2, "stroke-linejoin": "round" }, svg);
    if (small) { const t = el("text", { x: m.l + si * 150, y: 16, class: "strong" }, svg); t.textContent = `${name} (${F.int(s.n)})`; el("rect", { x: m.l + si * 150, y: 22, width: 26, height: 3, fill: col }, svg); }
    else { el("line", { x1: m.l + iw + 4, x2: m.l + iw + 4, y1: ends[si] - 9, y2: ends[si] + 16, stroke: col, "stroke-width": 3 }, svg); const t = el("text", { x: m.l + iw + 12, y: ends[si], class: "strong" }, svg); t.textContent = name; const t2 = el("text", { x: m.l + iw + 12, y: ends[si] + 15 }, svg); t2.textContent = `${F.int(s.n)} calls`; }
  });
  const cross = el("g", { opacity: 0 }, svg), cl = el("line", { y1: m.t, y2: m.t + ih, stroke: C.ink }, cross);
  const dots = series.map(([k, col]) => el("circle", { r: 4, fill: col, stroke: C.bg, "stroke-width": 2 }, cross));
  const hit = el("rect", { x: m.l, y: m.t, width: iw, height: ih, fill: "transparent", style: "touch-action:pan-y;cursor:crosshair" }, svg);
  const at = e => { const r = svg.getBoundingClientRect(), k = Math.max(0, Math.min(n - 1, Math.round(((e.clientX - r.left) / r.width * w - m.l) / iw * (n - 1))));
    cross.setAttribute("opacity", 1); cl.setAttribute("x1", x(k)); cl.setAttribute("x2", x(k)); series.forEach(([s], i) => { dots[i].setAttribute("cx", x(k)); dots[i].setAttribute("cy", y(P[s].mean[k])); });
    showTip(`<small>${k} trading days after the video</small>Recommended <b>${F.pp(P.buy.mean[k])}</b><small>range ${F.pp(P.buy.lo[k])} to ${F.pp(P.buy.hi[k])}</small>Warned about <b>${F.pp(P.bearish.mean[k])}</b><small>range ${F.pp(P.bearish.lo[k])} to ${F.pp(P.bearish.hi[k])}</small>`, e.clientX, e.clientY); };
  hit.addEventListener("pointermove", at); hit.addEventListener("pointerdown", at); hit.addEventListener("click", e => { e.stopPropagation(); at(e); });
  hit.addEventListener("pointerleave", e => { if (e.pointerType === "mouse") { cross.setAttribute("opacity", 0); hideTip(); } });
};
draw.placebo = () => {
  const host = $('[data-chart="placebo"]'), p = D.placebo["90"], w = widthOf(host), H = 200, m = { l: 4, r: 4, t: 30, b: 44 }, iw = w - m.l - m.r, ih = H - m.t - m.b;
  const lo = Math.min(p.edges[0], p.actual), hi = Math.max(p.edges[p.edges.length - 1], p.actual), x = lin(lo, hi, m.l, m.l + iw), max = Math.max(...p.hist), svg = svgIn(host, w, H, "Average 90-day result of the same calls on shifted dates");
  p.hist.forEach((c, i) => { const x0 = x(p.edges[i]), x1 = x(p.edges[i + 1]), hh = c / max * ih; el("rect", { x: x0 + .75, width: Math.max(1, x1 - x0 - 1.5), y: m.t + ih - hh, height: hh, fill: C.data2 }, svg); const hit = el("rect", { x: x0, width: x1 - x0, y: m.t, height: ih, fill: "transparent" }, svg); withTip(hit, `<b>${F.pp(p.edges[i])} to ${F.pp(p.edges[i + 1])}</b><small>${c} of ${p.n_shifts} alternative calendars</small>`); });
  el("line", { x1: m.l, x2: m.l + iw, y1: m.t + ih, y2: m.t + ih, stroke: C.ink }, svg);
  ticks(lo, hi, 5).forEach(v => { const t = el("text", { x: x(v), y: m.t + ih + 17, "text-anchor": "middle" }, svg); t.textContent = num(v); });
  const ax = el("text", { x: m.l + iw, y: H - 6, "text-anchor": "end" }, svg); ax.textContent = "average result against the S&P 500, in points →";
  el("line", { x1: x(p.actual), x2: x(p.actual), y1: m.t - 8, y2: m.t + ih, stroke: C.data, "stroke-width": 3 }, svg);
  const right = x(p.actual) < m.l + iw * .6, t = el("text", { x: x(p.actual) + (right ? 7 : -7), y: m.t - 12, "text-anchor": right ? "start" : "end", class: "strong" }, svg); t.textContent = `real dates: ${F.pp(p.actual)}`;
  $("#placeboNote").innerHTML = `On other dates the same calls averaged ${F.pp(p.placebo_mean)}. The real dates did better than only <b>${Math.round(p.percentile)}%</b> of the ${p.n_shifts} alternatives.`;
};

/* =====================================================================================
   page behaviour
   ===================================================================================== */
function page() {
  const links = $$(".steps a"), secs = links.map(a => $(a.getAttribute("href")));
  const spy = () => { let cur = -1; secs.forEach((s, i) => { if (s.getBoundingClientRect().top < innerHeight * 0.4) cur = i; }); links.forEach((a, i) => a.classList.toggle("on", i === cur)); };
  addEventListener("scroll", spy, { passive: true }); spy();
  const seen = new Map(), rt = {};
  const ro = new ResizeObserver(es => es.forEach(e => { const k = e.target.dataset.chart, w = Math.round(e.contentRect.width); if (seen.get(k) === w) return; seen.set(k, w); clearTimeout(rt[k]); rt[k] = setTimeout(() => draw[k](), 100); }));
  $$("[data-chart]").forEach(n => { seen.set(n.dataset.chart, Math.round(n.getBoundingClientRect().width)); ro.observe(n); });
}

hero(); rail(); sampleRows(); audit(); funnel(); Object.values(draw).forEach(f => f()); findingText(); page();
})();
