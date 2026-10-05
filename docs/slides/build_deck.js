// build_deck.js -- generates docs/slides/open-ai-silicon-osi.pptx   (v3: 150-minute hands-on workshop deck)
//
// Re-run after new metrics / images arrive:
//   NODE_PATH=<dir with node_modules containing pptxgenjs> \
//   PPTX_APPLY_THEME=<skill dir>/scripts/apply_theme.js node docs/slides/build_deck.js
//
// Every number is read at build time from designs/<design>/output/metrics.json and resources.json,
// build/results/_flow_stages/cnn_lite/{stages,tools}.json, or is a constant whose source is named in the
// slide's speaker notes. Images, used when present (labelled placeholders otherwise):
//   build/results/<design>/layout.png        build/results/_flow_stages/cnn_lite/*.png
//   build/labs/uart/reference/images/labN.png
// Speaker notes of every slide start with the planned minutes (block range + minutes for that slide).
//
// Style, derived from https://www.opensourceindia.in/ (page CSS / inline styles, fetched 2026-10-05):
//   FF4D2E  accent bullet dots in headings            -> ACCENT
//   008F7A on E1F0ED  audience chips                  -> TEAL / TEAL_TINT
//   12151C text of pills; F4F6F8 pill/section bg      -> INK / BG
//   071A36  dark navy in inline styles                -> NAVY
//   5C6573  muted body grey; D5D9DC divider grey      -> MUTED / LINE
// Site fonts are Space Grotesk (headings) and Inter (body), Google web fonts; the deck uses Arial (and Courier New for commands).

const fs = require("fs");
const path = require("path");
const pptxgen = require("pptxgenjs");

const REPO = path.resolve(__dirname, "..", "..");
const OUT = path.join(__dirname, "open-ai-silicon-osi.pptx");
const SKILL_THEME = process.env.PPTX_APPLY_THEME || "";

const C = {
  INK: "12151C", NAVY: "071A36", ACCENT: "FF4D2E", TEAL: "008F7A", TEAL_TINT: "E1F0ED",
  BG: "F4F6F8", WHITE: "FFFFFF", MUTED: "5C6573", LINE: "D5D9DC",
  WARN_TINT: "FFE9E4", WARN_TEXT: "B83A22", GREY_TINT: "E6E9ED",
};
const FONT = "Arial", MONO = "Courier New";
const THEME = {
  name: "Open AI Silicon (OSI style)", headFontFace: FONT, bodyFontFace: FONT,
  colors: { dk1: C.INK, lt1: C.WHITE, dk2: C.NAVY, lt2: C.BG, accent1: C.TEAL, accent2: C.ACCENT,
    accent3: C.MUTED, accent4: C.TEAL_TINT, accent5: C.LINE, accent6: C.WARN_TEXT, hlink: C.TEAL, folHlink: C.MUTED },
};

// ---------------------------------------------------------------- data access
const rj = (p) => { try { return JSON.parse(fs.readFileSync(path.join(REPO, p), "utf8").replace(/:\s*-?(Infinity|NaN)/g, ": null")); } catch (e) { return null; } };
const OLD_COUNT = { aes128: 32432, bnn_mnist: 34707 }; // instance counts of the original (pre-downsizing) builds
function load(d) {
  const m = rj(`designs/${d}/output/metrics.json`), r = rj(`designs/${d}/output/resources.json`);
  if (!m) return { m: null, r: null, pending: true };
  return { m, r, pending: OLD_COUNT[d] !== undefined && m["design__instance__count__stdcell"] === OLD_COUNT[d] };
}
const n0 = (x) => Number(x).toLocaleString("en-US");
const sg = (x) => (x >= 0 ? "+" : "−") + Math.abs(x).toFixed(2);
const wallFmt = (s) => (s < 120 ? `${Math.round(s)} s` : `${(s / 60).toFixed(1)} min`);
function worstCorner(m, kind) { // lowest slack over all corners, and the corner's name
  let best = null, name = "";
  for (const k of Object.keys(m)) { const mm = k.match(new RegExp(`^timing__${kind}__ws__corner:(.+)$`)); if (mm && typeof m[k] === "number" && (best === null || m[k] < best)) { best = m[k]; name = mm[1]; } }
  return { v: best, c: name.replace(/_(\d+C|n\d+C)_.*$/, "").replace("_025C", "") };
}
function statsFor(d) {
  const { m, r, pending } = load(d);
  if (!m || pending) return null;
  const bb = m["design__die__bbox"].split(" ").map(Number);
  return {
    cellsN: m["design__instance__count__stdcell"], cells: n0(m["design__instance__count__stdcell"]),
    die: `${+bb[2].toFixed(1)} × ${+bb[3].toFixed(1)}`,
    setup: m["timing__setup__ws"], hold: m["timing__hold__ws"],
    drcM: m["magic__drc_error__count"], drcK: m["klayout__drc_error__count"], lvs: m["design__lvs_error__count"],
    slew: m["design__max_slew_violation__count"], cap: m["design__max_cap_violation__count"],
    ant: m["antenna__violating__nets"], xor: m["design__xor_difference__count"],
    setupC: worstCorner(m, "setup").c, holdC: worstCorner(m, "hold").c,
    svio: m["timing__setup_vio__count"], hvio: m["timing__hold_vio__count"],
    peak: r ? r.container_peak_mem_gb : null, wallS: r ? r.wall_s_total : null, m,
  };
}
function findLayout(names) {
  for (const n of names) { const f = path.join(REPO, "build", "results", n, "layout.png"); if (fs.existsSync(f)) return f; }
  return null;
}
function pngSize(p) {
  try { const b = fs.readFileSync(p); if (b.slice(1, 4).toString() === "PNG") return [b.readUInt32BE(16), b.readUInt32BE(20)]; } catch (e) { /* none */ }
  return null;
}
// flow-stage snapshots: cnn_lite (the flow section follows one chip)
const STAGE_NAME = "cnn_lite";
const STAGE_DIR = path.join(REPO, "build", "results", "_flow_stages", STAGE_NAME);
const stageMeta = (() => { try { return JSON.parse(fs.readFileSync(path.join(STAGE_DIR, "stages.json"), "utf8")); } catch (e) { return null; } })();
const toolsMeta = (() => { try { return JSON.parse(fs.readFileSync(path.join(STAGE_DIR, "tools.json"), "utf8")); } catch (e) { return null; } })();
const stageImg = (name) => { const f = path.join(STAGE_DIR, name); return fs.existsSync(f) ? f : null; };
const stageInfo = (name) => (stageMeta && stageMeta.images ? stageMeta.images.find((x) => x.image === name) : null) || {};
const phaseS = (name) => { const p = toolsMeta && toolsMeta.phases ? toolsMeta.phases.find((x) => x.phase === name) : null; return p ? p.total_wall_s : null; };
const toolS = (phase, tool) => { const p = toolsMeta && toolsMeta.phases ? toolsMeta.phases.find((x) => x.phase === phase) : null; return p && p.time_by_tool_s ? p.time_by_tool_s[tool] : null; };
const stepS = (stepName) => { if (!toolsMeta) return null; const tail = stepName.replace(/^\d+-/, ""); for (const p of toolsMeta.phases) for (const s of p.steps) if (s.step.replace(/^\d+-/, "") === tail) return s.wall_s; return null; };
const CTS_N = ((stageInfo("04_cts.png").description || "").match(/the (\d+) clock buffers/) || [])[1] || "n/a";
const fs1 = (x) => (x === null || x === undefined ? "n/a" : x >= 100 ? `${Math.round(x)} s` : `${(+x).toFixed(1)} s`);

// ---------------------------------------------------------------- the 150-minute plan
const BLOCKS = [
  { id: "A", range: "0–10", from: 0, to: 10, name: "Why open-source silicon" },
  { id: "B", range: "10–40", from: 10, to: 40, name: "The open-source EDA flow" },
  { id: "C", range: "40–60", from: 40, to: 60, name: "Lab block 1" },
  { id: "D", range: "60–85", from: 60, to: 85, name: "Tour of the designs" },
  { id: "E", range: "85–105", from: 85, to: 105, name: "Lab block 2" },
  { id: "F", range: "105–125", from: 105, to: 125, name: "What verification caught" },
  { id: "G", range: "125–140", from: 125, to: 140, name: "Road to silicon" },
  { id: "H", range: "140–150", from: 140, to: 150, name: "Recap and questions" },
  { id: "X", range: "", from: 0, to: 0, name: "Appendix" },
];
const BL = Object.fromEntries(BLOCKS.map((b) => [b.id, b]));

// ---------------------------------------------------------------- deck + layouts
const pres = new pptxgen();
pres.layout = "LAYOUT_16x9"; // 10 x 5.625 in
pres.theme = { headFontFace: FONT, bodyFontFace: FONT };
pres.author = "Raja Gopal";
pres.title = "Open-source chips from RTL to GDSII: a 150-minute hands-on workshop";
pres.subject = "Prepared for Open Source India";

const FOOT = "open-ai-silicon  ·  Prepared for Open Source India";
pres.defineSlideMaster({
  title: "OSI_CONTENT", background: { color: C.BG },
  objects: [
    { text: { text: FOOT, options: { x: 0.5, y: 5.22, w: 6, h: 0.3, fontFace: FONT, fontSize: 10, color: C.MUTED, margin: 0 } } },
    { placeholder: { options: { name: "title", type: "title", x: 0.5, y: 0.3, w: 9.0, h: 1.05, fontFace: FONT, fontSize: 28, bold: true, color: C.INK, margin: 0, valign: "top", align: "left" }, text: "" } },
  ],
  slideNumber: { x: 8.9, y: 5.22, w: 0.6, h: 0.3, fontFace: FONT, fontSize: 10, color: C.MUTED, align: "right" },
});
pres.defineSlideMaster({
  title: "OSI_DARK", background: { color: C.NAVY },
  objects: [
    { text: { text: FOOT, options: { x: 0.7, y: 5.22, w: 6, h: 0.3, fontFace: FONT, fontSize: 10, color: "AEB8C8", margin: 0 } } },
    { placeholder: { options: { name: "title", type: "title", x: 0.7, y: 1.0, w: 8.4, h: 1.3, fontFace: FONT, fontSize: 36, bold: true, color: C.WHITE, margin: 0, valign: "top", align: "left" }, text: "" } },
  ],
  slideNumber: { x: 8.9, y: 5.22, w: 0.6, h: 0.3, fontFace: FONT, fontSize: 10, color: "AEB8C8", align: "right" },
});

let slideNo = 0;
const REG = []; // {slide, block, weight, notes}
const sections = new Set();
const sec = (t) => { if (!sections.has(t)) { pres.addSection({ title: t }); sections.add(t); } return t; };
function T(s, text, x, y, w, h, o = {}) {
  s.addText(text, Object.assign({ x, y, w, h, fontFace: FONT, fontSize: 18, color: C.INK, margin: 0, valign: "top", isTextBox: true }, o));
}
function content(title, block, notes, weight) {
  const s = pres.addSlide({ masterName: "OSI_CONTENT", sectionTitle: sec(BL[block].name) });
  s.addText(title, { placeholder: "title" });
  REG.push({ slide: s, block, weight: weight || 1, notes: notes || "" });
  slideNo++;
  return s;
}
function pill(s, text, x, y, w, kind, h) {
  const k = { ok: [C.TEAL_TINT, C.TEAL], warn: [C.WARN_TINT, C.WARN_TEXT], grey: [C.GREY_TINT, C.INK], dark: [C.ACCENT, C.WHITE], navy: [C.NAVY, C.WHITE] }[kind || "ok"];
  const hh = h || 0.32;
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h: hh, rectRadius: hh / 2, fill: { color: k[0] }, line: { color: k[0], width: 0.5 }, objectName: "pill " + text });
  s.addText(text, { x, y, w, h: hh, fontFace: FONT, fontSize: 12, bold: true, color: k[1], align: "center", valign: "middle", margin: 0, isTextBox: true });
}
function card(s, x, y, w, h, o = {}) {
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.08, fill: { color: o.fill || C.WHITE },
    line: { color: o.line || C.LINE, width: 0.75, dashType: o.dash || "solid" }, objectName: o.name || "card" });
}
function chain(s, labels, x, y, w, h, o = {}) {
  const n = labels.length, gap = o.gap || 0.3, bw = (w - gap * (n - 1)) / n;
  labels.forEach((l, i) => {
    const bx = x + i * (bw + gap);
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: bx, y, w: bw, h, rectRadius: 0.06, fill: { color: o.fill || C.TEAL_TINT }, line: { color: C.TEAL, width: 1 }, objectName: "block " + l.split("\n")[0] });
    T(s, l, bx, y, bw, h, { fontSize: o.fontSize || 14, align: "center", valign: "middle", bold: true, margin: 2 });
    if (i < n - 1) s.addShape(pres.shapes.LINE, { x: bx + bw + 0.03, y: y + h / 2, w: gap - 0.06, h: 0, line: { color: C.ACCENT, width: 2, endArrowType: "triangle" }, objectName: "arrow" });
  });
}
function imageBox(s, file, x, y, w, h, label, expected) {
  if (file) {
    s.addShape(pres.shapes.RECTANGLE, { x, y, w, h, fill: { color: C.WHITE }, line: { color: C.LINE, width: 0.75 } });
    const dim = pngSize(file); let iw = w - 0.1, ih = h - 0.1;
    if (dim) { const r = Math.min((w - 0.1) / dim[0], (h - 0.1) / dim[1]); iw = dim[0] * r; ih = dim[1] * r; }
    s.addImage({ path: file, x: x + (w - iw) / 2, y: y + (h - ih) / 2, w: iw, h: ih, altText: label });
    return true;
  }
  card(s, x, y, w, h, { fill: C.WHITE, dash: "dash", line: C.MUTED, name: "image placeholder" });
  T(s, "IMAGE PLACEHOLDER", x, y + h / 2 - 0.35, w, 0.3, { fontSize: 14, bold: true, color: C.MUTED, align: "center" });
  T(s, expected, x + 0.1, y + h / 2, w - 0.2, 0.5, { fontSize: 12, color: C.MUTED, align: "center" });
  return false;
}
function bigNumber(s, value, label, x, y, w, o = {}) {
  T(s, value, x, y, w, 0.5, { fontSize: o.size || 26, bold: true, color: o.color || C.INK, valign: "middle", fit: "shrink" });
  T(s, label, x, y + 0.5, w, 0.3, { fontSize: 14, color: C.MUTED });
}
function code(s, lines, x, y, w, h, size) {
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y, w, h, rectRadius: 0.06, fill: { color: C.NAVY }, line: { color: C.NAVY }, objectName: "command" });
  T(s, lines.join("\n"), x + 0.12, y, w - 0.2, h, { fontFace: MONO, fontSize: size || 16, bold: true, color: "FFFFFF", valign: "middle" });
}
// progress marker for dividers: 150-minute bar, current block highlighted
function progressBar(s, id) {
  const x0 = 0.7, w = 8.4, y = 4.2;
  BLOCKS.filter((b) => b.to > 0).forEach((b) => {
    const bx = x0 + (b.from / 150) * w, bw = ((b.to - b.from) / 150) * w - 0.03;
    const cur = b.id === id;
    s.addShape(pres.shapes.RECTANGLE, { x: bx, y, w: bw, h: 0.22, fill: { color: cur ? C.ACCENT : "2C3E5C" }, line: { color: cur ? C.ACCENT : "2C3E5C" }, objectName: "progress " + b.id });
  });
  const b = BL[id];
  T(s, `Minutes ${b.range} of 150`, 0.7, 4.5, 8.4, 0.4, { fontSize: 18, bold: true, color: C.WHITE });
}
function divider(id, sub, notes) {
  const s = pres.addSlide({ masterName: "OSI_DARK", sectionTitle: sec(BL[id].name) });
  s.addText(BL[id].name, { placeholder: "title" });
  if (sub) T(s, sub, 0.7, 2.35, 8.2, 0.9, { fontSize: 20, color: "D6DCE6" });
  progressBar(s, id);
  REG.push({ slide: s, block: id, weight: 0.3, notes });
  slideNo++;
  return s;
}

// ================================================================ BLOCK A (0-10): why open-source silicon
{
  const s = pres.addSlide({ masterName: "OSI_DARK", sectionTitle: sec(BL.A.name) });
  slideNo++;
  pill(s, "PREPARED FOR OPEN SOURCE INDIA", 0.7, 0.6, 3.6, "dark");
  s.addText("Open-source chips, from RTL to GDSII: a hands-on workshop", { placeholder: "title", y: 1.1 });
  T(s, "150 minutes: watch one chip take shape, run the flow yourself, and learn what verification caught", 0.7, 3.0, 8.0, 0.8, { fontSize: 18, color: "D6DCE6" });
  T(s, "Raja Gopal", 0.7, 4.0, 5, 0.35, { fontSize: 20, bold: true, color: C.WHITE });
  T(s, "github.com/rajaghv-dev/open-ai-silicon", 0.7, 4.4, 6, 0.3, { fontSize: 16, color: "7FD6C6" });
  REG.push({ slide: s, block: "A", weight: 1, notes: "Source: git config user.name = Raja Gopal; repository URL from README.md. Prepared for Open Source India; not issued by the conference. Say plainly: the repository is private at the moment of writing (see the status slide in block G)." });
}
{
  const s = content("Today in 150 minutes: eight blocks", "A",
    "Agenda. Block ranges are the owner's plan for the 150-minute session. docs/LABS.md has its own facilitator outline (labs of 15 minutes each, a break at 70-80); this deck follows the owner's block plan instead, so use LABS.md only for the lab worksheets.", 1.2);
  BLOCKS.filter((b) => b.to > 0).forEach((b, i) => {
    const y = 1.35 + i * 0.47;
    pill(s, b.range, 0.5, y + 0.03, 1.3, i % 2 ? "grey" : "ok", 0.36);
    T(s, b.name, 2.0, y, 7.4, 0.42, { fontSize: 20, bold: i === 2 || i === 4, valign: "middle" });
    if (i === 2 || i === 4) pill(s, "LAB", 8.5, y + 0.03, 0.9, "dark", 0.36);
  });
}
{
  const s = content("Open silicon lets anyone follow a chip from text to layout", "A",
    "Sources: README.md and CLAUDE.md (RTL to GDSII on SkyWater sky130A with LibreLane 3.0.2 and OpenROAD); docs/SPEC.md section 1 (goal: examples small enough for free compute). Talking points: the tools, the process data (PDK) and the designs are all public, so every step can be repeated and inspected.", 1);
  const cards = [["Open tools", "Yosys, OpenROAD, Magic, KLayout, Netgen"], ["An open PDK", "SkyWater sky130, 130 nm"], ["Open designs", "12 examples, simplest first"]];
  cards.forEach((c, i) => {
    const x = 0.5 + i * 3.05;
    card(s, x, 1.6, 2.9, 2.5);
    s.addShape(pres.shapes.OVAL, { x: x + 0.25, y: 1.85, w: 0.5, h: 0.5, fill: { color: C.TEAL_TINT }, line: { color: C.TEAL_TINT } });
    T(s, String(i + 1), x + 0.25, 1.85, 0.5, 0.5, { fontSize: 20, bold: true, color: C.TEAL, align: "center", valign: "middle" });
    T(s, c[0], x + 0.25, 2.5, 2.5, 0.4, { fontSize: 20, bold: true });
    T(s, c[1], x + 0.25, 2.95, 2.45, 1.0, { fontSize: 18, color: C.MUTED });
  });
  T(s, "Simulated and hardened in a container. Not fabricated.", 0.5, 4.4, 9, 0.4, { fontSize: 18, bold: true, color: C.WARN_TEXT });
}
{
  const s = content("You will leave having run the flow yourself", "A",
    "Sources: docs/LABS.md (six labs, one flow stage each, 3 to 65 seconds each, measured on uart under 2 CPUs / 8 GB) and docs/SPEC.md 5.8. Outcomes are the owner's block plan: see the flow, run it, tour the designs, learn what verification caught.", 1);
  [["1", "Watch one chip take shape"], ["2", "Run six flow stages yourself"], ["3", "Learn what verification caught"]].forEach((r, i) => {
    const y = 1.6 + i * 1.1;
    card(s, 0.5, y, 9.0, 0.9);
    s.addShape(pres.shapes.OVAL, { x: 0.75, y: y + 0.15, w: 0.6, h: 0.6, fill: { color: C.NAVY }, line: { color: C.NAVY } });
    T(s, r[0], 0.75, y + 0.15, 0.6, 0.6, { fontSize: 22, bold: true, color: C.WHITE, align: "center", valign: "middle" });
    T(s, r[1], 1.65, y, 7.6, 0.9, { fontSize: 22, bold: true, valign: "middle" });
  });
}
{
  const tight = ["spm_ref", "counter_wb", "uart", "ring_osc", "pwm_dac", "sigma_delta", "aes128", "bnn_mnist", "cnn_lite", "fcnn_mnist", "user_proj_example"].map(statsFor).filter((x) => x && x.peak !== null);
  const maxPeak = Math.max(...tight.map((x) => x.peak)), maxWall = Math.max(...tight.map((x) => x.wallS));
  const s = content("Every design is sized for a free-tier machine", "A",
    `Sources: docs/SPEC.md 5.4 (tight profile = 2 CPUs, 8 GB) and 5.1 (tiers); designs/<design>/output/resources.json container_peak_mem_gb and wall_s_total for the ${tight.length} designs rebuilt under the tight profile; docs/baseline/baseline_tight.md (Apple-silicon Colima VM, container capped to 2 CPUs / 8 GB; wall times are a lower bound for cloud x86). Colab, Codespaces: not verified.`);
  [["2", "CPUs"], ["8 GB", "memory cap"], [`${maxPeak.toFixed(2)} GB`, "largest peak"], [wallFmt(maxWall), "slowest build"]].forEach((n, i) => {
    const x = 0.5 + i * 2.28;
    card(s, x, 1.6, 2.15, 1.7);
    T(s, n[0], x + 0.1, 1.85, 1.95, 0.7, { fontSize: 30, bold: true, align: "center", valign: "middle", color: C.TEAL, fit: "shrink" });
    T(s, n[1], x + 0.1, 2.65, 1.95, 0.5, { fontSize: 18, color: C.MUTED, align: "center" });
  });
  ["Colab", "Codespaces", "GitHub Actions"].forEach((t, i) => pill(s, t, 0.5 + i * 1.8, 3.7, 1.65, "ok", 0.36));
  T(s, "A 2-CPU, 8 GB container stands in for the free tiers.", 0.5, 4.25, 9, 0.4, { fontSize: 18 });
  T(s, "Measured on an Apple-silicon VM; cloud machines will be slower.", 0.5, 4.7, 9, 0.35, { fontSize: 16, color: C.MUTED });
}

// ================================================================ BLOCK B (10-40): the EDA flow, watching cnn_lite
divider("B", "Watch one chip, cnn_lite, take shape: tools, steps, and the open PDK", "Block B: 30 minutes. Stage images, tools and step times are from build/results/_flow_stages/cnn_lite (stages.json, tools.json); the design is cnn_lite because it fills its die.");
{
  const s = content("The whole flow, from RTL to GDSII", "B",
    "Sources: README.md and CLAUDE.md (flow = LibreLane 3.0.2 + OpenROAD); build/results/_flow_stages/cnn_lite/tools.json (76 steps). No detail on this slide on purpose; each stage has its own slide next.", 1);
  const st = ["RTL", "netlist", "floor-\nplan", "placed", "clock\ntree", "routed", "checked", "GDSII"];
  const bw = 0.88, gap = 0.28;
  st.forEach((b, i) => {
    const x = 0.5 + i * (bw + gap), last = i === st.length - 1;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: 2.0, w: bw, h: 1.0, rectRadius: 0.08, fill: { color: last ? C.NAVY : C.TEAL_TINT }, line: { color: last ? C.NAVY : C.TEAL, width: 1 }, objectName: "stage " + b });
    T(s, b, x, 2.0, bw, 1.0, { fontSize: 12, bold: true, align: "center", valign: "middle", color: last ? C.WHITE : C.INK, margin: 1 });
    if (!last) s.addShape(pres.shapes.LINE, { x: x + bw + 0.03, y: 2.5, w: gap - 0.06, h: 0, line: { color: C.ACCENT, width: 2.25, endArrowType: "triangle" }, objectName: "flow arrow" });
  });
  T(s, "Every step is open source.", 0.5, 3.5, 9, 0.45, { fontSize: 22, bold: true });
  T(s, "LibreLane runs it. The sky130 open PDK makes it possible.", 0.5, 4.1, 9, 0.4, { fontSize: 18, color: C.MUTED });
}

// stage slides
function stageSlide(o) {
  const s = content(o.title, "B", o.notes, o.weight || 1);
  [["IN", o.inp, "ok"], ["OUT", o.out, "ok"], ["TOOL", o.tool, "dark"], ["TIME", o.time, "grey"]].forEach((r, i) => {
    const y = 1.5 + i * 0.84;
    pill(s, r[0], 0.5, y + 0.04, 0.85, r[2], 0.34);
    T(s, r[1], 1.5, y, 3.45, 0.8, { fontSize: 18, bold: i === 2, valign: "top" });
  });
  const bx = 5.15, by = 1.5, bw = 4.35, bh = 2.85;
  if (o.draw) o.draw(s, bx, by, bw, bh);
  else if (o.imgs && o.imgs.length > 1) {
    imageBox(s, o.imgs[0], bx, by, bw / 2 - 0.05, bh, o.title, "pending: " + STAGE_NAME + " stage image");
    imageBox(s, o.imgs[1], bx + bw / 2 + 0.05, by, bw / 2 - 0.05, bh, o.title, "pending: " + STAGE_NAME + " stage image");
  } else imageBox(s, o.imgs && o.imgs[0], bx, by, bw, bh, o.title, "pending: build/results/_flow_stages/" + STAGE_NAME + "/*.png");
  if (o.caption) T(s, o.caption, bx, by + bh + 0.08, bw, 0.7, { fontSize: 16, color: C.MUTED });
  return s;
}
const SNOTE = `Sources: build/results/_flow_stages/${STAGE_NAME}/stages.json (tool, step_wall_s, description per image) and tools.json (phase times; total ${toolsMeta ? toolsMeta.sum_of_step_wall_s : "n/a"} s over ${toolsMeta ? toolsMeta.total_steps : 76} steps, 2 CPUs / 8 GB, Apple-silicon VM); docs/SPEC.md section 4. `;
function descOf(name) { const i = stageInfo(name); return i.description ? `Image ${name}: ${i.description} Step directory: ${i.step_directory}. ` : ""; }
function drawSim(s, x, y, w, h) {
  card(s, x, y, w, h);
  chain(s, ["RTL\n(Verilog)", "testbench", "PASS /\nFAIL"], x + 0.2, y + 0.9, w - 0.4, 1.0, { fontSize: 14, gap: 0.35 });
}
function drawSynth(s, x, y, w, h) {
  card(s, x, y, w, h);
  T(s, "assign y = a & b;", x + 0.25, y + 0.3, w - 0.5, 0.45, { fontSize: 18, bold: true, align: "center", valign: "middle" });
  s.addShape(pres.shapes.LINE, { x: x + w / 2, y: y + 0.85, w: 0, h: 0.5, line: { color: C.ACCENT, width: 2, endArrowType: "triangle" } });
  s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: x + 0.4, y: y + 1.5, w: w - 0.8, h: 0.95, rectRadius: 0.06, fill: { color: C.TEAL_TINT }, line: { color: C.TEAL, width: 1 } });
  T(s, "sky130_fd_sc_hd__and2_1\n(a standard cell)", x + 0.4, y + 1.5, w - 0.8, 0.95, { fontSize: 14, bold: true, align: "center", valign: "middle" });
}
const flowStats = statsFor(STAGE_NAME);
function drawTiming(s, x, y, w, h) {
  const worst = (p) => flowStats ? Math.min(...["min", "nom", "max"].map((r) => flowStats.m[`timing__setup__ws__corner:${r}_${p}`]).filter((v) => typeof v === "number")) : null;
  [["slow corner", "100 °C, 1.60 V", worst("ss_100C_1v60")], ["typical", "25 °C, 1.80 V", worst("tt_025C_1v80")], ["fast corner", "−40 °C, 1.95 V", worst("ff_n40C_1v95")]].forEach((c, i) => {
    const yy = y + i * 0.97;
    card(s, x, yy, w, 0.88);
    T(s, c[0], x + 0.2, yy + 0.08, 2.0, 0.4, { fontSize: 18, bold: true });
    T(s, c[1], x + 0.2, yy + 0.5, 2.0, 0.3, { fontSize: 14, color: C.MUTED });
    T(s, c[2] === null ? "n/a" : `${sg(c[2])} ns`, x + 2.0, yy + 0.08, w - 2.2, 0.72, { fontSize: 24, bold: true, color: C.TEAL, align: "right", valign: "middle" });
  });
}
function drawChecks(s, x, y, w, h) {
  [["Magic DRC", "design rules"], ["KLayout DRC", "design rules"], ["Netgen LVS", "layout = netlist"], ["KLayout XOR", "two outputs agree"]].forEach((c, i) => {
    const yy = y + i * 0.72;
    card(s, x, yy, w, 0.64);
    T(s, c[0], x + 0.2, yy, 2.0, 0.64, { fontSize: 18, bold: true, valign: "middle" });
    T(s, c[1], x + 2.2, yy, w - 2.35, 0.64, { fontSize: 16, color: C.MUTED, valign: "middle" });
  });
}
const CAP_OUT = "Cell outlines (LEF), not full mask geometry.";
stageSlide({ title: "First, check that the RTL does what we mean", inp: "Verilog RTL and a testbench.", out: "A pass or fail verdict.", tool: "Verilator lints, Icarus Verilog simulates.", time: `Lint: ${fs1(phaseS("lint"))}`, draw: drawSim,
  notes: SNOTE + "Lint is step 01 (Verilator) of the flow; simulation is run separately with iverilog (make simulate-*)." });
stageSlide({ title: "Synthesis turns RTL into library cells", inp: "RTL and the cell library.", out: "A netlist of sky130 standard cells.", tool: "Yosys, with ABC.", time: `Synthesis step: ${fs1(stepS("06-yosys-synthesis"))}`, draw: drawSynth,
  notes: SNOTE + "Steps 05-09 (yosys). sky130_fd_sc_hd__and2_1 is a cell of the sky130_fd_sc_hd standard-cell library. Lab 1 runs this stage on uart: 674 cells." });
{
  const a = stageInfo("01_floorplan.png"), b = stageInfo("02_pdn.png");
  stageSlide({ title: "Floorplanning draws the die and its power grid", inp: "The netlist.", out: "A die, rows for cells, pins, a power grid.", tool: "OpenROAD.", time: `Floorplan ${fs1(a.step_wall_s)}, power ${fs1(b.step_wall_s)}`, imgs: [stageImg("01_floorplan.png"), stageImg("02_pdn.png")],
    caption: "Left: rows and pins. Right: power grid. " + CAP_OUT, notes: SNOTE + descOf("01_floorplan.png") + descOf("02_pdn.png") });
}
{
  const a = stageInfo("03a_global_placement.png"), b = stageInfo("03_placement.png");
  stageSlide({ title: "Placement gives every cell a position", inp: "The floorplanned netlist.", out: "Every cell on a legal site.", tool: "OpenROAD: global, then detailed.", time: `Global ${fs1(a.step_wall_s)}, detailed ${fs1(b.step_wall_s)}`, imgs: [stageImg("03a_global_placement.png"), stageImg("03_placement.png")],
    caption: "Left: rough. Right: legal. " + CAP_OUT, notes: SNOTE + descOf("03a_global_placement.png") + descOf("03_placement.png") });
}
{
  const a = stageInfo("04_cts.png");
  stageSlide({ title: "The clock tree reaches every flip-flop", inp: "The placed design.", out: "A buffered clock network.", tool: "OpenROAD (TritonCTS).", time: fs1(a.step_wall_s), imgs: [stageImg("04_cts.png")],
    caption: "Clock nets are straight lines between pins, not routed wires; layout dimmed.", notes: SNOTE + descOf("04_cts.png") });
}
{
  const a = stageInfo("05_routing.png");
  stageSlide({ title: "Routing draws the wires, and takes the longest", inp: "Placed cells and the clock tree.", out: "Every connection on metal layers.", tool: "OpenROAD: global, then detailed.", time: `${fs1(a.step_wall_s)} for detailed routing`, imgs: [stageImg("05_routing.png")],
    caption: "Real routed wires; the cells are drawn as outlines (LEF).", notes: SNOTE + descOf("05_routing.png") });
}
stageSlide({ title: "Timing is checked at every corner", inp: "The routed design.", out: "Setup and hold slack per corner.", tool: "OpenSTA, inside OpenROAD.", time: `OpenROAD in signoff: ${fs1(toolS("signoff", "OpenROAD"))}`, draw: drawTiming,
  notes: SNOTE + `Values: designs/${STAGE_NAME}/output/metrics.json, worst of min/nom/max extraction for each process corner (timing__setup__ws__corner:*_ss_100C_1v60, *_tt_025C_1v80, *_ff_n40C_1v95).` });
stageSlide({ title: "Verification asks: is the layout legal and correct?", inp: "The finished layout.", out: "Reports with zero errors.", tool: "Magic and KLayout (DRC), Netgen (LVS).", time: `Magic ${fs1(toolS("signoff", "Magic"))}, KLayout ${fs1(toolS("signoff", "KLayout"))}, Netgen ${fs1(toolS("signoff", "Netgen"))}`, draw: drawChecks,
  notes: SNOTE + "Times are the signoff phase per tool from tools.json (they include stream-out and XOR). Antenna checks run in OpenROAD." });
{
  stageSlide({ title: "Last, the layout is written out as GDSII", inp: "The routed, checked design.", out: "A GDSII file for the foundry.", tool: "Magic and KLayout write the stream.", time: `Stream-out: ${fs1((stepS("58-magic-streamout") || 0) + (stepS("59-klayout-streamout") || 0))}`, imgs: [stageImg("06_final.png"), stageImg("07_zoom_cells.png")],
    caption: "The real thing: every layer, transistors included. Right: a 60 × 40 µm zoom.", notes: SNOTE + descOf("06_final.png") + descOf("07_zoom_cells.png") });
}
{
  const n = toolsMeta ? toolsMeta.total_steps : 76;
  const s = content("LibreLane runs all of it, step by step", "B",
    `Sources: build/results/_flow_stages/${STAGE_NAME}/tools.json (total_steps ${n}); CLAUDE.md (LibreLane 3.0.2); docs/SPEC.md 4.1 and 5.4 (resources.json records peak memory and wall time per step).`);
  card(s, 0.5, 1.6, 3.0, 2.8, { fill: C.NAVY, line: C.NAVY });
  T(s, String(n), 0.5, 1.9, 3.0, 1.2, { fontSize: 72, bold: true, color: C.WHITE, align: "center", valign: "middle" });
  T(s, "steps in one run", 0.5, 3.2, 3.0, 0.5, { fontSize: 20, color: "D6DCE6", align: "center" });
  ["Runs Yosys, OpenROAD, Magic, KLayout and Netgen in order.", "Records metrics for every run.", "One config.json per design."].forEach((t, i) => {
    s.addShape(pres.shapes.OVAL, { x: 4.0, y: 1.8 + i * 0.95, w: 0.2, h: 0.2, fill: { color: C.ACCENT }, line: { color: C.ACCENT } });
    T(s, t, 4.4, 1.7 + i * 0.95, 5.1, 0.85, { fontSize: 18 });
  });
}
{
  const s = content("The sky130 open PDK is what makes it possible", "B",
    "Sources: README.md and CLAUDE.md (SkyWater sky130A 130 nm); docs/SPEC.md 4.2 (PDK pins). '5 metal layers' as given in the task brief for sky130 (met1 to met5); sky130_fd_sc_hd is the standard-cell library used by the flow.");
  [["130 nm", "process node"], ["open", "standard cells"], ["5", "metal layers"]].forEach((n, i) => {
    const x = 0.5 + i * 3.05;
    card(s, x, 1.7, 2.9, 2.3);
    T(s, n[0], x, 2.1, 2.9, 0.9, { fontSize: 44, bold: true, color: C.TEAL, align: "center", valign: "middle" });
    T(s, n[1], x, 3.15, 2.9, 0.5, { fontSize: 20, color: C.MUTED, align: "center" });
  });
  T(s, "Published by SkyWater, so anyone can design for it.", 0.5, 4.4, 9, 0.4, { fontSize: 18 });
}
// phase bar from tools.json (cnn_lite), or from resources.json of a design as fallback
function phaseBar(s, phases, y) {
  const cols = [C.TEAL, "2F5AAE", C.ACCENT, "9463AE", "BE574B", C.NAVY, "5C6573", "0E8F66"];
  const tot = phases.reduce((a, p) => a + p[1], 0) || 1;
  let x = 0.5;
  phases.forEach((p, i) => {
    const w = Math.max(0.04, 9.0 * p[1] / tot);
    s.addShape(pres.shapes.RECTANGLE, { x, y, w, h: 1.0, fill: { color: cols[i % cols.length] }, line: { color: C.WHITE, width: 0.75 }, objectName: "phase " + p[0] });
    x += w;
  });
  return { cols, tot };
}
{
  const ph = toolsMeta ? toolsMeta.phases.map((p) => [p.phase, p.total_wall_s]) : [];
  const tot = ph.reduce((a, p) => a + p[1], 0);
  const top = ph.map((p, i) => [p[1], i]).sort((a, b) => b[0] - a[0]);
  const rt = ph.find((p) => p[0] === "routing");
  const title = rt ? `Routing takes ${Math.round(100 * rt[1] / tot)}% of the build time` : "Where the time goes in one build";
  const s = content(title, "B",
    `Sources: build/results/_flow_stages/${STAGE_NAME}/tools.json phases (${ph.map((p) => `${p[0]} ${p[1]} s`).join("; ")}); sum_of_step_wall_s ${toolsMeta ? toolsMeta.sum_of_step_wall_s : "n/a"}. Phase boundaries by step number as defined in tools.json (routing includes fill insertion; signoff = RCX, STA, IR drop, stream-out, XOR, DRC, LVS). 2 CPUs / 8 GB on an Apple-silicon VM;`, 1.2);
  const { cols } = phaseBar(s, ph, 1.7);
  T(s, `${wallFmt(tot)} in total (${STAGE_NAME}, 2 CPUs, 8 GB)`, 0.5, 2.8, 9, 0.4, { fontSize: 16, color: C.MUTED });
  top.slice(0, 3).forEach(([v, i], k) => {
    const xx = 0.5 + k * 3.05;
    s.addShape(pres.shapes.RECTANGLE, { x: xx, y: 3.6, w: 0.3, h: 0.3, fill: { color: cols[i % cols.length] }, line: { color: cols[i % cols.length] } });
    T(s, ph[i][0], xx + 0.4, 3.52, 2.5, 0.4, { fontSize: 20, bold: true });
    T(s, `${Math.round(v)} s`, xx + 0.4, 3.95, 2.5, 0.4, { fontSize: 20, color: C.MUTED });
  });
}
{
  const names = ["spm_ref", "counter_wb", "uart", "ring_osc", "pwm_dac", "sigma_delta", "aes128", "bnn_mnist", "fcnn_mnist", "user_proj_example", "cnn_lite", "cnn_fp16"];
  const sts = names.map(statsFor).filter(Boolean);
  const sum = (k) => sts.reduce((a, x) => a + x[k], 0);
  const tiles = [["Magic DRC", sum("drcM")], ["KLayout DRC", sum("drcK")], ["LVS", sum("lvs")], ["Antenna", sum("ant")], ["XOR", sum("xor")]];
  const failing = sts.filter((x) => x.svio > 0 || x.hvio > 0).length;
  const slewN = sts.filter((x) => x.slew > 0).length;
  const s = content("Five automatic checks must all come back clean", "B",
    `Sources: designs/<design>/output/metrics.json keys magic__drc_error__count, klayout__drc_error__count, design__lvs_error__count, antenna__violating__nets, design__xor_difference__count, summed over the ${sts.length} designs with metrics (${names.join(", ")}). Timing: timing__setup_vio__count and timing__hold_vio__count > 0 for ${failing} design(s) (cnn_fp16). Max-slew violations are non-zero in ${slewN} of ${sts.length} designs; counts per design in the appendix. Setup the next block with: these checks passed on counter_wb and ring_osc too, while they were hollow (block F).`, 1.2);
  tiles.forEach((t, i) => {
    const x = 0.5 + i * 1.8, z = t[1] === 0;
    card(s, x, 1.7, 1.65, 1.9, { fill: z ? C.TEAL_TINT : C.WARN_TINT, line: z ? C.TEAL : C.WARN_TEXT });
    T(s, String(t[1]), x, 1.85, 1.65, 0.9, { fontSize: 44, bold: true, color: z ? C.TEAL : C.WARN_TEXT, align: "center", valign: "middle" });
    T(s, t[0], x, 2.85, 1.65, 0.5, { fontSize: 16, bold: true, align: "center" });
  });
  T(s, `Errors found, summed over ${sts.length} designs.`, 0.5, 3.9, 9, 0.4, { fontSize: 18 });
  T(s, `Timing too: ${sts.length - failing} of ${sts.length} designs meet setup and hold (not cnn_fp16).`, 0.5, 4.35, 9, 0.4, { fontSize: 18, color: C.MUTED });
}

// ================================================================ BLOCK C (40-60): lab block 1
const LAB_WHERE = "Run on this Mac's Linux VM (2 CPUs, 8 GB). Codespaces and Colab: not verified.";
const LAB_SRC = `Sources: docs/LABS.md (worksheet, reference values, commands) and docs/SPEC.md 5.8; commands are defined in mk/L1.mk (lab-setup, lab, lab-check, lab-list, lab-reset, lab-knob, lab-pack, lab-unpack). Reference values are for uart, measured on the Mac VM (arm64, 2 CPUs, 8 GB, profile tight); x86 machines, a real Codespace and Colab are NOT tested (LABS.md expects 1.5 to 2 times longer). `;
divider("C", "Setup, then labs 1 to 3. The labs use uart (small, so each stage takes seconds); numbers differ from the cnn_lite pictures.", "Block C: 20 minutes. Setup about 2 minutes (128 s measured), labs 3, 6 and 9 s each; the rest is predicting, looking and discussing. The labs run on uart, not cnn_lite, so every stage takes seconds.");
{
  const s = content("One setup, then six small labs", "C",
    LAB_SRC + "make lab-setup: reference run plus checkpoints, 128 s measured on uart. Labs 1 to 6 measured 3, 6, 9, 12, 32 and 65 s (wall, including container start). Each lab starts from its own checkpoint, so a failed lab never blocks the next. make lab-list shows the labs and your status.", 2);
  pill(s, "RUN", 0.5, 1.5, 0.9, "dark", 0.36);
  code(s, ["make lab-setup"], 1.55, 1.42, 4.0, 0.52, 18);
  T(s, "about 2 minutes, once", 5.8, 1.42, 3.7, 0.52, { fontSize: 18, valign: "middle", color: C.MUTED });
  const labs = [["1", "synth", "3 s"], ["2", "floorplan", "6 s"], ["3", "place", "9 s"], ["4", "clock", "12 s"], ["5", "route", "32 s"], ["6", "signoff", "65 s"]];
  labs.forEach((l, i) => {
    const bw = 1.4, gap = 0.12, x = 0.5 + i * (bw + gap);
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x, y: 2.45, w: bw, h: 1.5, rectRadius: 0.08, fill: { color: i < 3 ? C.TEAL_TINT : C.WHITE }, line: { color: C.TEAL, width: 1 }, objectName: "lab " + l[0] });
    T(s, "Lab " + l[0], x, 2.55, bw, 0.35, { fontSize: 18, bold: true, align: "center" });
    T(s, l[1], x, 2.95, bw, 0.35, { fontSize: 18, align: "center" });
    T(s, l[2], x, 3.4, bw, 0.4, { fontSize: 20, bold: true, color: C.TEAL, align: "center" });
  });
  T(s, "Each lab starts from its own checkpoint.", 0.5, 4.2, 9, 0.4, { fontSize: 18, bold: true });
  T(s, LAB_WHERE, 0.5, 4.7, 9, 0.35, { fontSize: 16, color: C.MUTED });
}
{
  const r = rj("designs/uart/output/resources.json");
  const groups = [["lint, synth", 1, 9], ["floorplan", 10, 27], ["placement", 28, 34], ["clock tree", 35, 38], ["routing", 39, 53], ["signoff", 54, 76]];
  const ph = groups.map(([n, a, b]) => [n, r ? r.steps.filter((x) => { const k = parseInt(x.step.slice(0, 2), 10); return k >= a && k <= b; }).reduce((q, x) => q + x.wall_s, 0) : 0]);
  const s = content("While setup runs, it executes all 76 steps once", "C",
    `Sources: designs/uart/output/resources.json (76 steps, wall_s per step, grouped by step number: ${groups.map((g) => `${g[0]} ${g[1]}-${g[2]}`).join("; ")}); docs/LABS.md (setup 128 s measured on the Mac VM). Use the time to talk through the stages.`, 1.5);
  phaseBar(s, ph, 1.7);
  T(s, "The reference run of uart, step by step", 0.5, 2.85, 9, 0.4, { fontSize: 18, color: C.MUTED });
  [["Reference run", "all 76 steps"], ["Checkpoints", "saved before each lab"], ["Your results", "build/labs/uart/mine"]].forEach((c, i) => {
    const x = 0.5 + i * 3.05;
    card(s, x, 3.45, 2.9, 1.2);
    T(s, c[0], x + 0.15, 3.55, 2.6, 0.4, { fontSize: 18, bold: true });
    T(s, c[1], x + 0.15, 4.0, 2.65, 0.55, { fontSize: 16, color: C.MUTED });
  });
}
function labImage(n) { const f = path.join(REPO, "build", "labs", "uart", "reference", "images", `lab${n}.png`); return fs.existsSync(f) ? f : null; }
const uartS = statsFor("uart");
const LAB_UART = `Labs use uart (${uartS ? uartS.cells : "2,026"} cells, whole flow about 2 min): numbers differ from cnn_lite.`;
function labSlide(o) {
  const s = content(o.title, o.block || "C", LAB_SRC + `Labs run on uart, the flow section shows cnn_lite: reference numbers differ (e.g. lab 4: 32 clock buffers on uart; cnn_lite's clock tree has ${CTS_N} buffers/loads in stages.json). Predict: ${o.predictNote} Why (ask the room): ${o.why} If it fails: ${o.fail}`, 3);
  [["PREDICT", o.predict, 1.35, 0.7], ["RUN", null, 2.1, 0.9], ["LOOK AT", o.look, 3.05, 0.65], ["REFERENCE", o.ref, 3.75, 0.7]].forEach((r) => {
    pill(s, r[0], 0.5, r[2] + 0.04, 1.15, r[0] === "RUN" ? "dark" : "ok", 0.34);
    if (r[1] !== null) T(s, r[1], 1.8, r[2], 4.45, r[3], { fontSize: 18 });
  });
  code(s, o.cmd, 1.8, 2.1, 4.45, 0.85, 18);
  const img = o.image ? labImage(o.n) : null;
  if (o.image) imageBox(s, img, 6.45, 1.35, 3.05, 3.05, `Reference picture of lab ${o.n} (uart)`, `pending: build/labs/uart/reference/images/lab${o.n}.png`);
  else { card(s, 6.45, 1.35, 3.05, 3.05, { fill: C.TEAL_TINT, line: C.TEAL }); T(s, o.bigVal, 6.45, 1.85, 3.05, 1.0, { fontSize: 44, bold: true, color: C.TEAL, align: "center", valign: "middle" }); T(s, o.bigLabel, 6.55, 2.95, 2.85, 1.0, { fontSize: 18, color: C.MUTED, align: "center" }); }
  T(s, LAB_UART, 0.5, 4.55, 9, 0.3, { fontSize: 16, bold: true });
  T(s, LAB_WHERE, 0.5, 4.9, 9, 0.3, { fontSize: 16, color: C.MUTED });
  return s;
}
labSlide({ n: 1, title: "Lab 1: how many cells does Verilog become?", predict: "3 small Verilog files become 50, 700 or 5,000 cells?", predictNote: "50, 700 or 5,000 cells?", cmd: ["make lab N=1", "make lab-check N=1"], look: "Cells, cell area, unmapped cells.", ref: "674 cells, 6,147 µm², 0 unmapped, 0 latches.",
  image: false, bigVal: "674", bigLabel: "cells after synthesis (uart)", why: "Why is the cell area (6,147 um^2) much smaller than the die area seen in lab 2?", fail: "Log is build/labs/uart/mine/lab1/lab.log; usually run make lab-setup again." });
labSlide({ n: 2, title: "Lab 2: a die with rows and a power grid", predict: "The die is 300 × 300 µm. What fraction will cells fill?", predictNote: "what fraction of the 300 x 300 um die will the cells fill?", cmd: ["make lab N=2", "make lab-check N=2"], look: "Die, core, rows, power-grid violations.", ref: "102 rows, 0 violations, utilization 0.095.",
  image: true, why: "Why does the power grid exist before any signal wire?", fail: "Run make lab-check N=2; if the checkpoint is missing run make lab-setup or make lab-unpack." });
labSlide({ n: 3, title: "Lab 3: do cells spread out or cluster?", predict: "Will cells spread over the die, or cluster?", predictNote: "spread or cluster?", cmd: ["make lab N=3", "make lab-check N=3"], look: "Cells placed, wirelength, setup slack.", ref: "1,912 cells, 15,404 µm wire, +17.908 ns.",
  image: true, why: "The cells cluster on the right. What pulled them there? (Hint: where are the IO pins?)", fail: "Placement failure means the die is too full; see knob util." });

// ================================================================ BLOCK D (60-85): tour of the designs
divider("D", "Small to large: two light slides each", "Block D: 25 minutes, about one minute per slide. Standard cells = design__instance__count__stdcell (includes tap cells, not fill).");
const TB = "2 CPUs / 8 GB, Apple-silicon VM; not fabricated.";
const _fp = statsFor("cnn_fp16"), _cl = statsFor("cnn_lite");
const RATIO = _fp && _cl ? Math.round(_fp.cellsN / _cl.cellsN) : null;
const designs = [
  { key: "spm_ref", title: "spm_ref multiplies two numbers one bit at a time", what: "An 8-bit multiplicand times a serial multiplier; the product streams out one bit per clock.",
    diagram: ["x bit\n(serial)", "8 AND gates +\ncarry-save adders", "product bit\n(serial)"], check: "Checked: 67,286 multiplications, 0 errors.", before: "Was broken: the original did not multiply (3 × 3 gave 3).",
    long: "Self-checking testbench compares every product bit with a x b computed in the testbench: 67,286 multiplications, 1,094,197 checks, 0 errors (iverilog); gate-level simulation of the synthesized netlist also passes (build/v4/gl_all.log). Original RTL did not multiply; rewritten with the same ports.",
    sources: "Sources: designs/spm_ref/src/spm.v header; tests/testbenches/spm_tb.v (run: 'spm_tb: 67286 multiplications, 1094197 bit/status checks, 0 errors'); build/v4/gl_all.log (spm_ref gate-level PASS); docs/SPEC.md 6.2 (original did not multiply); designs/spm_ref/output/metrics.json and resources.json." },
  { key: "counter_wb", title: "counter_wb is a counter you can read over a bus", what: "A 32-bit counter behind a Wishbone slave; the logic-analyzer output mirrors the count.",
    diagram: ["Wishbone\nbus", "32-bit counter\n+ control", "la_data_out\n[31:0]"], check: "Checked: RTL tests and gate-level simulation.", before: "Was hollow: 1 of about 67 flip-flops survived. Fixed.",
    long: "RTL testbench passes five checks (initial value, write/read, reset, free-running count, logic-analyzer mirror); gate-level simulation of the synthesized netlist passes (build/v4/gl_all.log). The original assigned its registers in two always blocks, so synthesis kept 1 of about 67 flip-flops (build/audit/AUDIT.md); now all in one block (RTL header), 67 sequential cells in output/counter_wb.",
    sources: "Sources: rtl/examples/counter_wb/counter_wb.v header (all registers in one always block); build/audit/AUDIT.md (1 of ~67 flip-flops, 34 'multiple conflicting drivers'); build/v4/gl_all.log (counter_wb gate-level PASS); designs/counter_wb/output/metrics.json (design__instance__count__class:sequential_cell = 67, synthesis__check_error__count = 0) and resources.json." },
  { key: "uart", title: "uart sends and receives bytes over two wires", what: "A full-duplex 8N1 UART behind a Wishbone slave; the baud divider is a parameter.",
    diagram: ["Wishbone\nregisters", "uart_tx and\nuart_rx", "io_out[0] TX\nio_in[1] RX"], check: "Checked: loopback of 34 bytes through the Wishbone UART.", before: "Lab design: you run its flow in the labs.",
    long: "The committed uart_tb exercises uart_tx and uart_rx only (loopback, CLKS_PER_BIT = 5). The gate-level runs use 34 Wishbone loopback bytes (uart_wb_tb) and pass on the synthesized and the routed netlist (build/v4/gl_all.log, gl_final.log).",
    sources: "Sources: rtl/examples/uart/uart_wb.v, uart_tx.v, uart_rx.v headers; tests/testbenches/uart_tb.v (compiles only uart_tx.v + uart_rx.v); build/v4/gl_all.log (uart: '32 Wishbone loopback bytes incl. 00/FF', PASS); mk/checks.mk; designs/uart/output/metrics.json and resources.json." },
  { key: "ring_osc", title: "ring_osc counts the beats of a ring of inverters", what: "A ring of 31 inverting stages; a divider and counter tally its edges. Enable register at +0x08.",
    diagram: ["31-stage\nring", "divider +\nsynchroniser", "toggle count\n(Wishbone)"], check: "Checked: RTL tests and a gate-level test on the real cell ring.", before: "Was hollow: synthesis deleted the whole ring. Fixed.",
    long: "The original ring (a combinational loop of not primitives) was folded away by Yosys; the RTL test used a divider instead of the ring (build/audit/AUDIT.md). Now the ring is built from explicit sky130 nand2_1 / inv_1 cells (RTL header) and the gate-level test runs on the real cell ring and passes (build/v4/gl_all.log). Oscillation frequency in silicon remains unmeasured.",
    sources: "Sources: rtl/examples/ring_osc/ring_osc_wb.v header (structure, why explicit cells, 'one counted edge = 64 ring cycles'); build/audit/AUDIT.md (ring absent from the netlist); build/v4/gl_all.log (ring_osc: 'committed tb on the real cell ring', PASS); designs/ring_osc/output/metrics.json and resources.json." },
  { key: "pwm_dac", title: "pwm_dac turns a number into a duty cycle", what: "An 8-bit counter is compared with a duty register; a filter turns the pulses into a voltage.",
    diagram: ["duty register\n(Wishbone)", "counter <\nduty ?", "pwm_out"], check: "Checked: 0%, 50% and 255/256 duty tests pass.",
    long: "Testbench passes: duty 0 always low, duty 255 gives 255/256 high, duty 128 gives exactly 50%, duty register reads back (iverilog). Gate-level simulation passes.",
    sources: "Sources: rtl/examples/pwm_dac/pwm_dac_wb.v header; tests/testbenches/pwm_dac_tb.v; build/v4/gl_all.log (pwm_dac PASS); designs/pwm_dac/output/metrics.json and resources.json." },
  { key: "sigma_delta", title: "sigma_delta turns a number into a bit stream", what: "A 16-bit accumulator whose carry is a 1-bit stream; the density of ones tracks the input.",
    diagram: ["input\n16 bit", "accumulator\ncarry = out", "1-bit stream +\n256-cycle count"], check: "Checked: density tests pass at 0, half and full scale.",
    long: "Testbench passes: input 0 gives all zeros, 0xFFFF all ones, 0x8000 about 50%, density register near 128, input register reads back (iverilog). Gate-level simulation passes.",
    sources: "Sources: rtl/examples/sigma_delta/sigma_delta_wb.v header; tests/testbenches/sigma_delta_tb.v; build/v4/gl_all.log (sigma_delta PASS); designs/sigma_delta/output/metrics.json and resources.json." },
  { key: "aes128", title: "aes128 encrypts a block in 51 clock cycles", what: "A compact AES-128 core: four shared S-boxes and an on-the-fly round key.",
    diagram: ["state ⊕ key", "4 S-boxes +\nMixColumns", "128-bit\nstate"], check: "Checked: FIPS-197 vectors and 2,000 random blocks.", before: "Was broken: the original gave wrong ciphertext; 32,432 cells.",
    long: "Passes FIPS-197 Appendix B and C.1 and the all-zero vector, plus 1,000 random vectors through the core and 1,000 through Wishbone: 0 mismatches (iverilog); gate-level simulation passes (build/v4/gl_all.log). Original build: 32,432 cells, 800 x 800 um, setup -6.20 ns, wrong ciphertext for both NIST vectors.",
    sources: "Sources: rtl/examples/aes128/aes128_core.v header; tests/testbenches/aes128_tb.v (run: KAT 0/1/2 PASS, random 1000 core + 1000 wb, 'aes128 testbench: PASS (0 errors)'); build/v4/gl_all.log (aes128 PASS); docs/SPEC.md section 3 and 6.2/6.3 (51 cycles; excluding tap cells 7,288); docs/baseline/baseline_tight.md (original: 32,432 cells, -6.20 ns); designs/aes128/output/metrics.json and resources.json (new build)." },
  { key: "bnn_mnist", title: "bnn_mnist recognises digits with 1-bit arithmetic", what: "A 196 → 32 → 10 binarized network: XNOR and count, then a 4-bit score per digit.",
    diagram: ["196 pixel\nbits", "32 hidden\nXNOR + count", "10 scores", "class\n(argmax)"], check: "Checked: all 10,000 test images match; 90.10% accurate.", before: "Was untrained: random weights; 34,707 cells.",
    long: "RTL equals the integer reference on all 10,000 MNIST test images (130,302 checks, 0 errors). Accuracy 90.10% (9,010 / 10,000). 770 cycles per inference; 7,552 ROM bits. Gate-level simulation of 200 images passes. Before: 784 -> 32 -> 10 with random weights, 34,707 cells, 900 x 900 um, setup -6.62 ns.",
    sources: "Sources: designs/bnn_mnist/rtl/bnn_top.v header; build/sim/full.log ('RTL accuracy: 9010/10000 = 90.10%', 'total checks: 130302, errors: 0'); designs/bnn_mnist/model/weights.json; docs/SPEC.md 7.2, 7.4 and section 3; build/v4/gl_all.log (bnn_mnist PASS, 200 images); docs/baseline/baseline_tight.md (original 34,707 cells, -6.62 ns); designs/bnn_mnist/output/metrics.json and resources.json (new build)." },
  { key: "fcnn_mnist", imageNames: ["fcnn_mnist", "user_proj_example"], title: "fcnn_mnist recognises digits with one neuron layer", what: "One 196-to-10 fixed-point layer. user_proj_example is the same RTL, routed up to met4 only.",
    diagram: ["196 gray\npixels", "10 neurons\nserial MAC", "sigmoid\nlookup", "max finder\n(class)"], check: "Checked: 1,000 images match the bit-exact model; 91.72% accurate.", before: "Before: 784 inputs, 16-bit, 102,091 cells.",
    long: "1,000 MNIST images through Wishbone: 0 mismatches against the bit-exact reference, 909 / 1,000 correct, 602 cycles each. On 10,000 images the model gives 92.08% in floating point and 91.72% hardware-exact. Gate-level simulation of 100 images passes on the synthesized and routed netlists (build/v4). user_proj_example: same RTL, RT_MAX_LAYER met4; see appendix.",
    sources: "Sources: designs/fcnn_mnist/rtl/user_proj_example.v, designs/fcnn_mnist/model/golden.py header; designs/fcnn_mnist/model/weights.json (float_test_accuracy 0.9208, hw_test_accuracy 0.9172); designs/fcnn_mnist/tb/fcnn_mnist_tb.v (re-run, 1000 images: 0 mismatches, 909/1000, 602 cycles); build/v4/gl_all.log and gl_final.log (fcnn_mnist and user_proj_example: 100 images, PASS); docs/SPEC.md 6.2 (original 102,091 and 56,517 cells); designs/user_proj_example/config.json RT_MAX_LAYER met4; output/fcnn_mnist and output/user_proj_example metrics.json + resources.json." },
  { key: "cnn_lite", title: "cnn_lite recognises digits with a tiny convolution", what: "Four 3 × 3 binary filters, a 2 × 2 pool, then a dense layer over 144 features.",
    diagram: ["14 × 14\nbits", "conv 3×3\n4 filters", "2×2 pool\n144 bits", "dense\n144 → 10"], check: "Checked: all 10,000 images match; 91.37% accurate.", before: `About ${RATIO} times fewer cells than cnn_fp16.`,
    long: `RTL equals the reference on all 10,000 images (150,048 checks, 0 errors); accuracy 91.37% (9,137 / 10,000); 8 filters would reach 94.43% at twice the ROM; 1,587 cycles per inference; gate-level simulation of 200 images passes. cnn_fp16 has ${_fp ? _fp.cells : "n/a"} cells; cnn_lite ${_cl ? _cl.cells : "n/a"} (ratio about ${RATIO}).`,
    sources: `Sources: designs/cnn_lite/rtl/cnn_lite_top.v header; build/sim/cnn_lite_full.log ('10000 vectors, 0 errors', 150048 checks); docs/SPEC.md 6.5 (91.37%, 94.43% with 8 filters); build/v4/gl_all.log (cnn_lite PASS, 200 images, latency 1587); designs/cnn_lite/output/metrics.json vs designs/cnn_fp16/output/metrics.json (ratio ${_fp ? _fp.cellsN : "n/a"}/${_cl ? _cl.cellsN : "n/a"}).` },
  { key: "cnn_fp16", title: "cnn_fp16 is the large float16 reference we keep", what: "Conv2D, MaxPool and Dense in float16, generated by HLS. Not downsized; workstation only.",
    diagram: ["image", "Conv2D", "MaxPool", "Dense\nfloat16"], check: "Not re-verified here: no self-checking test in the repo.", before: "Fails timing at the slow corner (−5.18 ns).",
    workstation: true,
    long: "108 KB of HLS-generated Verilog; the HLS tool is not available so it cannot be regenerated. Hardened on a workstation (about 3.5 hours, 23 GB peak memory), 0 DRC, 0 LVS errors; setup -5.18 ns and hold -0.49 ns at the worst corner.",
    sources: "Sources: CLAUDE.md and docs/SPEC.md 3 and 6.2 (108 KB HLS Verilog, cannot be regenerated, ~32 GB); designs/cnn_fp16/output/metrics.json (531,505 cells, 2800 x 1760, setup -5.18, hold -0.49, slew 142,619, cap 2,207, 5,843 setup and 8 hold failing paths); docs/RESULTS.md (peak 23 GB, ~3.5 hours). No layout.png exists for it." },
];
designs.forEach((D) => {
  const st = statsFor(D.key);
  { // (a) what it does
    const s = content(D.title, "D", D.sources + " Detail: " + D.long, 1);
    T(s, D.what, 0.5, 1.5, 9, 0.8, { fontSize: 18 });
    chain(s, D.diagram, 0.5, 2.45, 9, 1.05, { fontSize: 14 });
    T(s, D.check, 0.5, 3.8, 9, 0.7, { fontSize: 18, bold: true, color: C.TEAL });
    if (D.before) T(s, D.before, 0.5, 4.4, 9, 0.6, { fontSize: 18, color: D.before.startsWith("Was") ? C.WARN_TEXT : C.MUTED });
  }
  { // (b) the layout
    const s = content(`Here is ${D.key} as a finished layout`, "D", D.sources, 1);
    const img = findLayout(D.imageNames || [D.key]);
    imageBox(s, img, 0.5, 1.5, 5.6, 3.3, `Layout of ${D.key}`, `pending: build/results/${(D.imageNames || [D.key])[0]}/layout.png`);
    const worst = st ? Math.min(st.setup, st.hold) : null;
    const nums = st ? [
      [st.cells, "standard cells", C.INK],
      [st.die + " µm", "die size", C.INK],
      [D.workstation ? "~3.5 h*" : (st.wallS !== null ? wallFmt(st.wallS) : "n/a"), D.workstation ? "build time (workstation)" : "build time, 2 CPUs / 8 GB", C.INK],
      [sg(worst) + " ns", `worst slack (${st.setup < st.hold ? "setup" : "hold"})`, worst < 0 ? C.WARN_TEXT : C.INK],
    ] : [["pending", "standard cells"], ["pending", "die size"], ["pending", "build time"], ["pending", "worst slack"]].map((x) => [x[0], x[1], C.WARN_TEXT]);
    nums.forEach((n, i) => bigNumber(s, n[0], n[1], 6.4, 1.5 + i * 0.85, 3.1, { color: n[2] }));
    const prov = "";
    const status = !st ? "Build pending." : (D.workstation ? "Workstation only (*docs/RESULTS.md); fails timing at the slow corner. Not fabricated." : TB + prov);
    T(s, status, 0.5, 4.88, 9, 0.3, { fontSize: 16, color: st ? C.MUTED : C.WARN_TEXT, bold: !st });
  }
});

// ================================================================ BLOCK E (85-105): lab block 2
divider("E", "Labs 4 to 6, then a knob experiment. Still uart: for example 32 clock buffers in lab 4.", "Block E: 20 minutes. Labs take 12, 32 and 65 s; the clock-period experiment about 55 s per value.");
labSlide({ n: 4, block: "E", title: "Lab 4: how many buffers does a clock tree need?", predict: "How many clock buffers for about 100 flip-flops?", predictNote: "how many clock buffers does a tree for about 100 flip-flops need?", cmd: ["make lab N=4", "make lab-check N=4"], look: "Clock buffers, slack, skew.", ref: "32 buffers, skew 0.259 ns, hold +0.309 ns.",
  image: true, why: "Why does CTS care about hold slack but placement did not?", fail: "Hold or slew failures show as negative slack; the lab still completes." });
labSlide({ n: 5, block: "E", title: "Lab 5: is the routed wire longer than the estimate?", predict: "Routed wire versus lab 3's estimate: shorter, equal, longer?", predictNote: "routed wire length versus the lab 3 estimate: shorter, equal or longer?", cmd: ["make lab N=5", "make lab-check N=5"], look: "Routed wirelength, violations.", ref: "18,764 µm routed; 0 routing, 0 antenna violations.",
  image: true, why: "Why is the final wire longer than the placement estimate?", fail: "The route may be too congested (see knob util); the message names the step. About 30 s: talk while it runs." });
labSlide({ n: 6, block: "E", title: "Lab 6: which signoff check fails first?", predict: "DRC, LVS or XOR: which is likeliest to fail?", predictNote: "which of DRC, LVS, XOR is most likely to fail on a flow-generated design?", cmd: ["make lab N=6", "make lab-check N=6"], look: "DRC, LVS, XOR, three corners.", ref: "All four checks 0; setup +16.5 / +13.2 / +17.7 ns.",
  image: true, why: "Which corner is worst for setup, and why?", fail: "A DRC or LVS count above 0 is a real finding, not a tooling error; open mine/lab6/runs/lab/final/ reports. About 1 minute: talk while it runs. Reference corners: tt 16.521, ss 13.230, ff 17.667 ns." });
{
  const s = content("Experiment: tighten the clock until slack goes negative", "E",
    LAB_SRC + "Knob clock on uart: 25 ns: slack +13.194; 10 ns: +1.086; 6 ns: -0.228 ns with 51 failing paths (LABS.md knob table; SPEC 5.8). Each run about 55 s; runs on a copy of the config, designs/*/config.json is not touched. A failing run is a valid result. Ask: what did we ask for that the chip could not give?", 3);
  pill(s, "RUN", 0.5, 1.45, 0.9, "dark", 0.36);
  code(s, ["make lab-knob KNOB=clock VALUE=10", "make lab-knob KNOB=clock VALUE=6"], 1.55, 1.38, 5.9, 0.85, 18);
  T(s, "about 55 s each", 7.6, 1.38, 1.9, 0.85, { fontSize: 16, color: C.MUTED, valign: "middle" });
  [["25 ns", "+13.194 ns", false], ["10 ns", "+1.086 ns", false], ["6 ns", "−0.228 ns", true]].forEach((c, i) => {
    const x = 0.5 + i * 3.05;
    card(s, x, 2.5, 2.9, 1.7, { fill: c[2] ? C.WARN_TINT : C.WHITE, line: c[2] ? C.WARN_TEXT : C.LINE });
    T(s, c[0], x, 2.6, 2.9, 0.4, { fontSize: 18, bold: true, align: "center", color: C.MUTED });
    T(s, c[1], x, 3.05, 2.9, 0.7, { fontSize: 30, bold: true, align: "center", valign: "middle", color: c[2] ? C.WARN_TEXT : C.TEAL });
    T(s, c[2] ? "51 failing paths" : "slack", x, 3.75, 2.9, 0.35, { fontSize: 16, align: "center", color: C.MUTED });
  });
  T(s, "Clock period (uart): shorter clock, less slack.", 0.5, 4.35, 9, 0.4, { fontSize: 18, bold: true });
  T(s, LAB_WHERE, 0.5, 4.88, 9, 0.3, { fontSize: 16, color: C.MUTED });
}
{
  const s = content("More knobs: each change has a measured cost", "E",
    LAB_SRC + "Knob table in docs/LABS.md: synthesis strategy on uart AREA 0: 674 cells, AREA 3: 968, DELAY 0: 740 (make lab-knob KNOB=synth \"VALUE=AREA 3\"); placement density 70: routed wirelength 18764 -> 18054 um (-3.8 percent; global-route wirelength went UP 0.9 percent, one design and one change: do not over-claim); utilization on spm_ref: 40 passes, 75 fails in the repair step after global placement (DPL-0036), 92 fails in global placement (GPL-0301). DELAY 1 is refused on purpose (out of memory at 16 GB or less).", 2);
  [["synth", "AREA 0 / AREA 3 / DELAY 0", "674 / 968 / 740 cells"], ["density", "make lab-knob KNOB=density VALUE=70", "routed wire −3.8%"], ["util", "VALUE=75, then 92 (spm_ref)", "fails: cannot legalize"]].forEach((r, i) => {
    const y = 1.5 + i * 1.1;
    card(s, 0.5, y, 9.0, 0.95);
    T(s, r[0], 0.7, y, 1.5, 0.95, { fontSize: 20, bold: true, valign: "middle" });
    T(s, r[1], 2.2, y, 4.2, 0.95, { fontSize: 16, valign: "middle", color: C.MUTED });
    T(s, r[2], 6.5, y, 2.9, 0.95, { fontSize: 18, bold: true, valign: "middle", color: C.TEAL, align: "right" });
  });
  T(s, "A failing run is a valid result.", 0.5, 4.85, 9, 0.35, { fontSize: 16, bold: true });
}

// ================================================================ BLOCK F (105-125): what verification caught
divider("F", "The originals' defects, then the hollow-design story and two new checks", "Block F: 20 minutes. Sources: docs/SPEC.md section 3 and build/audit/AUDIT.md (audit of 2026-10-05).");
{
  const s = content("The originals had real bugs; we fixed them", "F",
    "Sources: docs/SPEC.md section 3 and 6.2; the commit message of d2e98d2 (git log); docs/baseline/baseline_tight.md; designs/cnn_fp16/output/metrics.json (setup -5.18, hold -0.49). 'Six small tests' = spm_ref, counter_wb, uart, ring_osc, pwm_dac, sigma_delta (SPEC section 3). Full table in the appendix.", 1.5);
  const rows = [["spm_ref", "did not multiply", "rewritten, tested"], ["aes128", "wrong ciphertext", "new core passes NIST"], ["bnn_mnist", "random weights", "trained, 90.10%"], ["fcnn_mnist", "accuracy never tested", "91.72% measured"], ["Six small tests", "could not fail", "now exit non-zero"], ["cnn_fp16", "fails slow-corner timing", "kept as reference"]];
  T(s, "FOUND", 3.0, 1.45, 3.0, 0.3, { fontSize: 14, bold: true, color: C.WARN_TEXT });
  T(s, "NOW", 6.4, 1.45, 3.0, 0.3, { fontSize: 14, bold: true, color: C.TEAL });
  rows.forEach((r, i) => {
    const y = 1.8 + i * 0.52;
    T(s, r[0], 0.5, y, 2.4, 0.45, { fontSize: 18, bold: true, valign: "middle" });
    T(s, r[1], 3.0, y, 3.3, 0.45, { fontSize: 18, valign: "middle", color: C.WARN_TEXT });
    T(s, r[2], 6.4, y, 3.1, 0.45, { fontSize: 18, valign: "middle", color: C.TEAL });
  });
}
{
  const s = content("A clean layout of an empty design", "F",
    "Sources: build/audit/AUDIT.md (Verdict: two of eleven cloud designs are hollow; counter_wb kept 1 flip-flop of ~67, ring_osc lost its 31-inverter ring; 'the flow, metrics, CI gate and RTL simulations all report them as clean'); docs/SPEC.md section 3. This is the best teaching story of the project: every check we ran agreed, and every check was blind to the same thing.", 1.5);
  pill(s, "PASSED", 0.5, 1.45, 1.2, "ok", 0.36);
  ["RTL simulation", "DRC", "LVS", "Timing"].forEach((t, i) => {
    card(s, 0.5 + i * 2.28, 1.95, 2.15, 0.9, { fill: C.TEAL_TINT, line: C.TEAL });
    T(s, t, 0.5 + i * 2.28, 1.95, 2.15, 0.9, { fontSize: 18, bold: true, align: "center", valign: "middle", color: C.TEAL });
  });
  pill(s, "INSIDE", 0.5, 3.1, 1.2, "warn", 0.36);
  card(s, 0.5, 3.6, 9.0, 0.9, { fill: C.WARN_TINT, line: C.WARN_TEXT });
  T(s, "almost nothing: the logic had been removed", 0.5, 3.6, 9.0, 0.9, { fontSize: 24, bold: true, align: "center", valign: "middle", color: C.WARN_TEXT });
  T(s, "Found by an audit on 2026-10-05, in counter_wb and ring_osc.", 0.5, 4.7, 9, 0.4, { fontSize: 16, color: C.MUTED });
}
{
  const s = content("counter_wb kept 1 flip-flop of about 67", "F",
    "Sources: build/audit/AUDIT.md finding 1 (counter, running, overflow assigned in two always blocks; Yosys 'multiple conflicting drivers' x34; 1 FF kept; 32 bits of wbs_dat_o and 128 la_data_out bits tied to constants; hidden by ERROR_ON_SYNTH_CHECKS=false); rtl/examples/counter_wb/counter_wb.v header ('every register ... is assigned in the ONE always block below'); designs/counter_wb/output/metrics.json (design__instance__count__class:sequential_cell = 67, design__instance__count__stdcell = 1,214, was 750 in docs/baseline/baseline_tight.md; synthesis__check_error__count = 0).", 1.5);
  const rows = [["Original", 1, C.WARN_TEXT], ["Fixed", 67, C.TEAL]];
  const sm = statsFor("counter_wb");
  rows.forEach((r, i) => {
    const y = 1.7 + i * 1.0, w = Math.max(0.12, 6.4 * r[1] / 67);
    T(s, r[0], 0.5, y, 1.5, 0.7, { fontSize: 20, bold: true, valign: "middle" });
    s.addShape(pres.shapes.RECTANGLE, { x: 2.1, y: y + 0.05, w, h: 0.6, fill: { color: r[2] }, line: { color: r[2] }, objectName: "bar " + r[0] });
    T(s, String(r[1]), 2.1 + w + 0.15, y, 1.2, 0.7, { fontSize: 24, bold: true, valign: "middle", color: r[2] });
  });
  T(s, "flip-flops that survived synthesis", 0.5, 3.75, 9, 0.35, { fontSize: 16, color: C.MUTED });
  T(s, "Cause: two always blocks drove the same registers.", 0.5, 4.2, 9, 0.4, { fontSize: 18, bold: true });
  T(s, sm ? `Now ${sm.cells} standard cells (was 750).` : "", 0.5, 4.65, 9, 0.4, { fontSize: 18, color: C.MUTED });
}
{
  const sr = statsFor("ring_osc");
  const s = content("ring_osc lost its entire ring of inverters", "F",
    "Sources: build/audit/AUDIT.md finding 2 (the (* keep *) not-primitive ring was dropped; no combinational cycle in the netlist; RTL test replaced the ring with a divider); rtl/examples/ring_osc/ring_osc_wb.v header (Yosys folds pairs of $not cells and deletes the loop; ring now built from explicit sky130_fd_sc_hd__nand2_1 and __inv_1 instances, dont-touch in the config; counted edge = 64 ring cycles); build/v4/gl_all.log (ring_osc gate-level test on the real cell ring PASS); designs/ring_osc/output/metrics.json (new build) vs docs/baseline/baseline_tight.md (709 cells).", 1.5);
  chain(s, ["31 inverting\nstages", "Yosys folds\nand deletes", "no ring in\nthe netlist"], 0.5, 1.7, 9, 1.2, { fontSize: 16, gap: 0.5 });
  T(s, "66 of 77 flip-flops found; the RTL test used a divider.", 0.5, 3.3, 9, 0.4, { fontSize: 18 });
  T(s, "Fixed: the ring is built from explicit sky130 cells.", 0.5, 3.8, 9, 0.4, { fontSize: 18, bold: true, color: C.TEAL });
  T(s, sr ? `Now ${sr.cells} standard cells (was 709).` : "", 0.5, 4.3, 9, 0.4, { fontSize: 18, color: C.MUTED });
}
{
  const s = content("Two new permanent checks guard against hollow designs", "F",
    "Sources: mk/checks.mk header (make gl-<name>: synthesis-only LibreLane run on a copy, then the design's self-checking testbench on the sky130 netlist with the sky130_fd_sc_hd functional models; make check-<name>: scripts/flow/check_signoff.py, DRC/LVS/XOR/antenna/slack/synthesis checks and 'surviving flip-flops >= flip-flops the RTL elaborates to'; gl-all and check-all run every design; names: spm counter uart aes128 ring-osc pwm-dac sigma-delta bnn fcnn user-proj cnn-lite). docs/SPEC.md 6.6 and 3. These commands have been run on the Mac VM only.", 2);
  [["pattern: gl-<name>, e.g. gl-counter", "Simulate the synthesized netlist", "make gl-counter"], ["pattern: check-<name>, e.g. check-counter", "Surviving registers = RTL registers", "make check-counter"]].forEach((r, i) => {
    const y = 1.5 + i * 1.5;
    card(s, 0.5, y, 9.0, 1.3);
    code(s, [r[2]], 0.7, y + 0.2, 4.1, 0.55, 18);
    T(s, r[1], 5.0, y + 0.1, 4.3, 0.75, { fontSize: 20, bold: true, valign: "middle" });
    T(s, r[0], 0.7, y + 0.85, 4.1, 0.35, { fontSize: 14, color: C.MUTED });
  });
  T(s, "Run on this Mac's Linux VM only.", 0.5, 4.7, 9, 0.4, { fontSize: 16, color: C.MUTED });
}
{
  const rd = (f) => { try { return fs.readFileSync(path.join(REPO, "build", "v4", f), "utf8"); } catch (e) { return ""; } };
  const allLog = rd("gl_all.log"), finLog = rd("gl_final.log"), chkLog = rd("check_all.log");
  const synthRows = allLog.split("\n").filter((l) => /^\S+ \| synth:/.test(l));
  const synthPass = synthRows.filter((l) => /\| PASS \|/.test(l)).length;
  const finRows = finLog.split("\n").filter((l) => /^\S+ rc=\d+$/.test(l));
  const finPass = finRows.filter((l) => /rc=0$/.test(l)).length;
  const chkRows = chkLog.split("\n").filter((l) => /^\S+\s+\d+\s+\d+\s+\d+\s+(PASS|FAIL)/.test(l));
  const chkPass = chkRows.filter((l) => /PASS$/.test(l.trim())).length;
  const regs = chkRows.map((l) => l.trim().split(/\s+/)[1]).join(", ");
  const s = content(`Both new checks pass for all ${chkRows.length || 11} designs`, "F",
    `Sources: build/v4/gl_all.log (gl-all on the synthesized netlist: ${synthPass} of ${synthRows.length} PASS; vectors: spm_ref exhaustive 8x8 plus random, 67,286 multiplications; uart_wb 34 Wishbone loopback bytes; aes128 3 known-answer vectors plus 1,000 core and 1,000 Wishbone vectors; bnn_mnist 200 vectors; cnn_lite 200; fcnn_mnist and user_proj_example 100 images each; counter_wb, ring_osc, pwm_dac, sigma_delta their committed testbenches); build/v4/gl_final.log (same testbenches on the post-route netlist: ${finPass} of ${finRows.length} rc=0), build/v4/check_all.log (make check-all: registers the RTL describes equal the sequential cells that survive: ${regs} for the designs in table order spm_ref, counter_wb, uart, aes128, ring_osc, pwm_dac, sigma_delta, bnn_mnist, fcnn_mnist, user_proj_example, cnn_lite; ${chkPass} of ${chkRows.length} PASS). Written by an independent verifier. Before the fix: counter_wb kept 1 of about 67 flip-flops and ring_osc lost its ring (66 flip-flops found, ring absent), build/audit/AUDIT.md. Commands: make gl-<name>, make check-<name> (mk/checks.mk).`, 1.5);
  [[`${synthPass} of ${synthRows.length}`, "gate-level, synthesized netlist"], [`${finPass} of ${finRows.length}`, "gate-level, routed netlist"], [`${chkPass} of ${chkRows.length}`, "registers survive"]].forEach((t, i) => {
    const x = 0.5 + i * 3.05, ok = t[0].split(" ")[0] === t[0].split(" ")[2];
    card(s, x, 1.55, 2.9, 1.9, { fill: ok ? C.TEAL_TINT : C.WARN_TINT, line: ok ? C.TEAL : C.WARN_TEXT });
    T(s, t[0], x, 1.7, 2.9, 0.9, { fontSize: 36, bold: true, align: "center", valign: "middle", color: ok ? C.TEAL : C.WARN_TEXT });
    T(s, t[1], x + 0.15, 2.65, 2.6, 0.7, { fontSize: 18, bold: true, align: "center" });
  });
  T(s, "Before the fix", 0.5, 3.7, 3.0, 0.35, { fontSize: 16, bold: true, color: C.WARN_TEXT });
  T(s, "counter_wb: 1 of about 67 flip-flops kept", 0.5, 4.05, 9, 0.4, { fontSize: 18 });
  T(s, "ring_osc: the whole ring gone from the netlist", 0.5, 4.5, 9, 0.4, { fontSize: 18 });
}

// ================================================================ BLOCK G (125-140): road to silicon
divider("G", "Run it yourself afterwards; then Caravel and ChipIgnite, clearly marked as planned", "Block G: 15 minutes.");
{
  const s = content("make lunch: one unattended command", "G",
    "Sources: mk/lunch.mk and scripts/flow/lunch.sh (check the environment, install what is missing, simulate, harden reuse-first, check-<name>, gl-<name> NETLIST=final on the routed netlist, collect, pack build/lunch/open-ai-silicon-lunch_<design>.tar.gz); README.md and docs/SETUP.md: bnn_mnist in Docker mode 7 min 48 s end to end on the development Mac, 14 s on a re-run; Docker-free in a fresh ubuntu:22.04 container (2 CPUs, 8 GB, no Docker, arm64): spm_ref 191 s including a 138 s tool install, bnn_mnist 437 s. Uses Docker when a daemon answers, otherwise the Nix-installed tools. Not measured on x86, Colab or a real Codespace.", 2);
  pill(s, "RUN", 0.5, 1.5, 0.9, "dark", 0.36);
  code(s, ["make lunch"], 1.55, 1.42, 3.2, 0.52, 18);
  T(s, "bnn_mnist by default", 5.0, 1.42, 4.5, 0.52, { fontSize: 18, valign: "middle", color: C.MUTED });
  [["7 min 48 s", "bnn_mnist, Docker, development Mac"], ["14 s", "the same, on a re-run"], ["437 s", "bnn_mnist, Docker-free Ubuntu container"]].forEach((n, i) => {
    const x = 0.5 + i * 3.05;
    card(s, x, 2.3, 2.9, 1.7);
    T(s, n[0], x, 2.45, 2.9, 0.8, { fontSize: 32, bold: true, color: C.TEAL, align: "center", valign: "middle" });
    T(s, n[1], x + 0.15, 3.3, 2.6, 0.65, { fontSize: 16, color: C.MUTED, align: "center" });
  });
  T(s, "Container: 2 CPUs, 8 GB, arm64. A re-run skips finished stages.", 0.5, 4.3, 9, 0.4, { fontSize: 18 });
}
{
  const s = content("Running it yourself: where things stand today", "G",
    "Sources: owner statement 2026-10-05 and docs/AGENTS_QUICKREF.md / README.md: the repository is PRIVATE; the Colab notebook notebooks/open_ai_silicon_rtl2gds.ipynb exists and its cells ran as a script in a Docker-free Ubuntu container, but it has NOT been run on Colab; nothing has run in a real Codespace (.devcontainer/devcontainer.json exists); GitHub Actions: the test and RTL-simulation jobs have passed twice on GitHub (gh run list: ci success, two runs); the hardening, gate-level and lunch workflows have not run there. docs/WORKSHOP_HOSTING.md for the routes.", 2);
  [["GitHub Actions", "tests and RTL simulation passed twice; hardening, gate-level and lunch workflows not run there", "ok"], ["Colab notebook", "exists; ran as a script in a container, not run on Colab", "warn"], ["Codespaces", "nothing run in a real Codespace", "warn"], ["Repository", "private", "warn"]].forEach((r, i) => {
    const y = 1.45 + i * 0.9;
    pill(s, r[2] === "ok" ? "DONE" : "OPEN", 0.5, y + 0.2, 0.9, r[2], 0.36);
    T(s, r[0], 1.6, y, 2.3, 0.8, { fontSize: 20, bold: true, valign: "middle" });
    T(s, r[1], 3.95, y, 5.55, 0.8, { fontSize: 16, valign: "middle", color: C.MUTED });
  });
  T(s, "The labs have run on one Mac's Linux VM only.", 0.5, 4.95, 9, 0.3, { fontSize: 16, bold: true, color: C.WARN_TEXT });
}
{
  const s = content("Next, a design could go to silicon through ChipIgnite", "G",
    "Sources: docs/SPEC.md section 8 (8.2 wrapper rules, die 2920 x 3520 um; 8.4 hardening, macro about 400 x 400 um; 8.5 precheck, 14 checks; 8.6 submission) and section 12 (open decision 3). Repository has no chipignite/ directory (checked 2026-10-05). PLANNED, NOT DONE.");
  pill(s, "PLANNED, NOT DONE", 0.5, 1.45, 2.4, "warn", 0.36);
  chain(s, ["Harden\nbnn_mnist", "Wrap in\nCaravel", "Pass\nprecheck", "Submit\n(a human)"], 0.5, 2.1, 9.0, 1.4, { fontSize: 16, gap: 0.3, fill: C.WHITE });
  T(s, "The wrapper and the precheck are not done yet.", 0.5, 4.0, 9, 0.4, { fontSize: 18, bold: true, color: C.WARN_TEXT });
}
{
  const names = ["spm_ref", "counter_wb", "uart", "ring_osc", "pwm_dac", "sigma_delta", "aes128", "bnn_mnist", "fcnn_mnist", "user_proj_example", "cnn_lite"];
  const slewList = names.filter((n) => { const x = statsFor(n); return x && x.slew > 0; });
  const s = content("What stands between this and a shuttle", "G",
    `Sources: docs/SPEC.md section 8 (wrapper, precheck) and 6.1 (target: max-slew and max-cap reported); mk/checks.mk (gl-<name>, check-<name>); build/v4/gl_all.log, gl_final.log, check_all.log (11 of 11); designs/<design>/output/metrics.json design__max_slew_violation__count > 0 for: ${slewList.join(", ")} (plus cnn_fp16, a workstation reference). No chipignite/ directory exists. Nothing fabricated.`, 1.5);
  [["Caravel wrapper and ChipFoundry precheck", "not started", "warn"], ["Gate-level and no-logic-lost checks", "in place", "ok"], ["Max-slew violations", "remain in " + slewList.length + " designs", "warn"], ["Fabricated", "nothing", "warn"]].forEach((r, i) => {
    const y = 1.45 + i * 0.8;
    pill(s, r[2] === "ok" ? "DONE" : "OPEN", 0.5, y + 0.2, 0.9, r[2], 0.36);
    T(s, r[0], 1.6, y, 4.9, 0.75, { fontSize: 18, bold: true, valign: "middle" });
    T(s, r[1], 6.6, y, 2.9, 0.75, { fontSize: 18, valign: "middle", color: C.MUTED });
  });
  T(s, slewList.join(", "), 0.5, 4.75, 9, 0.45, { fontSize: 16, color: C.MUTED });
}
{
  const s = content("A shuttle slot costs money and needs a decision", "G",
    "Sources: docs/SPEC.md 8.7 'Shuttle facts' (web research of 2026-10-04, marked unverified): CI2612 commitment 2026-11-04, tapeout 2026-12-07, delivery 2027-05-25; $14,950 per project (100 packaged parts or bare die, an evaluation board); $500 non-refundable deposit; a shuttle needs 20 projects; no standing free shuttle found. docs/SPEC.md 12: whether to buy a slot is an open owner decision.");
  [["$14,950", "per project"], ["2026-12-07", "tapeout date"], ["20", "projects needed"]].forEach((n, i) => {
    const x = 0.5 + i * 3.05;
    card(s, x, 1.6, 2.9, 1.9);
    T(s, n[0], x, 1.9, 2.9, 0.8, { fontSize: 30, bold: true, color: C.TEAL, align: "center", valign: "middle", fit: "shrink" });
    T(s, n[1], x, 2.8, 2.9, 0.5, { fontSize: 18, color: C.MUTED, align: "center" });
  });
  T(s, "Shuttle CI2612. Facts from web research, not re-verified.", 0.5, 3.9, 9, 0.4, { fontSize: 18 });
  T(s, "Whether to buy a slot is still the owner's decision.", 0.5, 4.4, 9, 0.4, { fontSize: 18, bold: true });
}

// ================================================================ BLOCK H (140-150): recap and questions
{
  const s = content("Three things to take away", "H",
    "Recap: (1) the flow is a sequence of small stages you can run and look at (blocks B, C, E); (2) open tools plus an open PDK make every step inspectable; (3) clean DRC, LVS and timing do not prove the logic survived: count what survives and simulate the netlist (block F).", 1.5);
  [["1", "A chip flow is small steps you can run and inspect"], ["2", "Open tools and an open PDK make every step visible"], ["3", "Clean checks are not enough: verify the logic survived"]].forEach((r, i) => {
    const y = 1.5 + i * 1.1;
    card(s, 0.5, y, 9.0, 0.9);
    s.addShape(pres.shapes.OVAL, { x: 0.75, y: y + 0.15, w: 0.6, h: 0.6, fill: { color: C.NAVY }, line: { color: C.NAVY } });
    T(s, r[0], 0.75, y + 0.15, 0.6, 0.6, { fontSize: 22, bold: true, color: C.WHITE, align: "center", valign: "middle" });
    T(s, r[1], 1.65, y, 7.7, 0.9, { fontSize: 20, bold: true, valign: "middle" });
  });
}
{
  const hardened = ["spm_ref", "counter_wb", "uart", "ring_osc", "pwm_dac", "sigma_delta", "aes128", "bnn_mnist", "cnn_lite", "fcnn_mnist", "user_proj_example"].filter((d) => { const x = statsFor(d); return x && x.peak !== null; }).length;
  const pendList = ["aes128", "bnn_mnist"].filter((d) => !statsFor(d));
  const s = content("Twelve designs, one open flow, nothing fabricated yet", "H",
    `Sources: designs/ (12 design directories); designs/<design>/output/resources.json present for ${hardened} designs rebuilt under the tight profile; no chip has been fabricated (docs/SPEC.md section 8, 12). Standard cells = design__instance__count__stdcell, which counts tap cells but not fill (docs/SPEC.md 6.2); excluding tap cells bnn_mnist is 5,828 and aes128 7,288. Full tables in the appendix. `, 1.2);
  [["12", "designs"], [String(hardened), "rebuilt on 2 CPUs / 8 GB"], [String(pendList.length), "builds pending"], ["0", "chips fabricated"]].forEach((n, i) => {
    const x = 0.5 + i * 2.28, w = i === 2 && pendList.length;
    card(s, x, 1.5, 2.15, 2.1, { fill: w ? C.WARN_TINT : C.WHITE, line: w ? C.WARN_TEXT : C.LINE });
    T(s, n[0], x, 1.7, 2.15, 0.9, { fontSize: 44, bold: true, color: w ? C.WARN_TEXT : C.TEAL, align: "center", valign: "middle" });
    T(s, n[1], x + 0.1, 2.7, 1.95, 0.8, { fontSize: 18, color: C.MUTED, align: "center" });
  });
  T(s, "Standard cells = design__instance__count__stdcell:", 0.5, 3.85, 9, 0.4, { fontSize: 18, bold: true });
  T(s, "it counts tap cells, not fill.", 0.5, 4.3, 9, 0.4, { fontSize: 18 });
}
{
  const s = content("Where to go next: run it again, on your own", "H",
    "Sources: mk/L1.mk and mk/checks.mk (commands); docs/LABS.md; repository URL from README.md (private at the moment of writing). Commands shown exist in the repository's make files.", 1);
  code(s, ["make lunch", "make lab-setup", "make lab N=1", "make gl-counter"], 0.5, 1.45, 5.2, 1.9, 18);
  T(s, "One run, the labs, the gate-level check", 5.95, 1.45, 3.55, 1.9, { fontSize: 18, valign: "middle", color: C.MUTED });
  T(s, "github.com/rajaghv-dev/open-ai-silicon", 0.5, 3.7, 9, 0.5, { fontSize: 24, bold: true, color: C.TEAL });
  T(s, "The repository is private for now.", 0.5, 4.35, 9, 0.4, { fontSize: 18, color: C.WARN_TEXT, bold: true });
}
{
  const s = pres.addSlide({ masterName: "OSI_DARK", sectionTitle: sec(BL.H.name) });
  s.addText("Questions", { placeholder: "title" });
  T(s, "Raja Gopal  ·  Prepared for Open Source India", 0.7, 2.4, 8, 0.4, { fontSize: 20, color: C.WHITE });
  T(s, "github.com/rajaghv-dev/open-ai-silicon", 0.7, 2.9, 8, 0.5, { fontSize: 24, bold: true, color: "7FD6C6" });
  progressBar(s, "H");
  REG.push({ slide: s, block: "H", weight: 1.5, notes: "Questions. Sources: README.md (repository URL); git config user.name." });
  slideNo++;
}

// ================================================================ APPENDIX
{
  const s = pres.addSlide({ masterName: "OSI_DARK", sectionTitle: sec("Appendix") });
  s.addText("Appendix", { placeholder: "title" });
  T(s, "Full tables and command reference", 0.7, 2.35, 8, 0.6, { fontSize: 20, color: "D6DCE6" });
  REG.push({ slide: s, block: "X", weight: 1, notes: "Appendix: detail removed from the main slides; not presented." });
  slideNo++;
}
const ALL = ["spm_ref", "counter_wb", "uart", "ring_osc", "pwm_dac", "sigma_delta", "aes128", "bnn_mnist", "fcnn_mnist", "user_proj_example", "cnn_lite", "cnn_fp16"];
const hd = (t) => ({ text: t, options: { bold: true, color: C.WHITE, fill: { color: C.NAVY }, fontSize: 10, fontFace: FONT, align: "center" } });
const cl = (t, o = {}) => ({ text: String(t), options: Object.assign({ fontSize: 10, fontFace: FONT, color: C.INK, fill: { color: C.WHITE }, align: "center" }, o) });
const pe = (t) => cl(t, { color: C.WARN_TEXT, italic: true, fill: { color: C.WARN_TINT } });
function appTable(title, header, rowFn, notes, foot) {
  const s = content(title, "X", notes);
  const rows = [header.map(hd)];
  ALL.forEach((k) => { const st = statsFor(k); rows.push([cl(k, { bold: true, align: "left" })].concat(rowFn(st, k).map((c) => (c === null ? pe("pending") : c)))); });
  s.addTable(rows, { x: 0.5, y: 1.0, w: 9.0, colW: [1.7].concat(header.length === 9 ? [0.85, 1.1, 0.85, 0.7, 0.85, 0.7, 0.85, 0.7] : Array(header.length - 1).fill(7.3 / (header.length - 1))), border: { type: "solid", pt: 0.5, color: C.LINE }, rowH: 0.26, valign: "middle", margin: [0, 0.05, 0, 0.05], fontSize: 10 });
  if (foot) T(s, foot, 0.5, 4.95, 9, 0.25, { fontSize: 10, color: C.MUTED });
  return s;
}
appTable("Appendix: build numbers per design", ["Design", "Std cells*", "Die (\u00b5m)", "Setup ns", "at", "Hold ns", "at", "Peak mem", "Wall"],
  (st, k) => {
    if (!st) return [null, null, null, null, null, null, null, null];
    const ws = k === "cnn_fp16";
    return [cl(st.cells), cl(st.die), cl(sg(st.setup), st.setup < 0 ? { color: C.WARN_TEXT, bold: true } : {}), cl(st.setupC, { color: C.MUTED }), cl(sg(st.hold), st.hold < 0 ? { color: C.WARN_TEXT, bold: true } : {}), cl(st.holdC, { color: C.MUTED }),
      cl(ws ? "23 GB\u2020" : st.peak !== null ? st.peak.toFixed(2) + " GB" : "n/a"), cl(ws ? "~3.5 h\u2020" : st.wallS !== null ? wallFmt(st.wallS) : "n/a")];
  },
  "Sources: designs/<design>/output/metrics.json (design__instance__count__stdcell, design__die__bbox, timing__setup__ws__corner:* and timing__hold__ws__corner:*: worst over all corners, corner named) and resources.json; same values as scripts/docs_tables.py. Tight profile: 2 CPUs, 8 GB, Apple-silicon VM; cloud x86 will be slower. cnn_fp16 peak/wall: docs/RESULTS.md workstation run.",
  "*Std cells: design__instance__count__stdcell (taps in, fill out). \u2020cnn_fp16: docs/RESULTS.md. ss = slow, ff = fast corner.");
appTable("Appendix: signoff counts per design", ["Design", "Magic DRC", "KLayout", "LVS", "Antenna", "XOR", "Max-slew", "Max-cap"],
  (st) => (st ? [cl(st.drcM), cl(st.drcK), cl(st.lvs), cl(st.ant), cl(st.xor), cl(n0(st.slew), st.slew ? { color: C.WARN_TEXT } : {}), cl(n0(st.cap), st.cap ? { color: C.WARN_TEXT } : {})] : [null, null, null, null, null, null, null]),
  "Sources: designs/<design>/output/metrics.json keys magic__drc_error__count, klayout__drc_error__count, design__lvs_error__count, antenna__violating__nets, design__xor_difference__count, design__max_slew_violation__count, design__max_cap_violation__count.",
  "Orange = remaining max-slew / max-cap violations. ");
[0, 6].forEach((off, part) => {
  const s = content(`Appendix: how each design was verified (${part + 1} of 2)`, "X", "Sources: see each design's slide notes; testbench runs of tests/testbenches/*; build/v4/gl_all.log.");
  const rows = [[hd("Design"), hd("Verification")]];
  designs.slice(off, off + 6).forEach((D) => rows.push([cl(D.key, { bold: true, align: "left" }), cl(D.long, { align: "left", fontSize: 10 })]));
  s.addTable(rows, { x: 0.5, y: 1.2, w: 9.0, colW: [1.5, 7.5], border: { type: "solid", pt: 0.5, color: C.LINE }, rowH: 0.3, valign: "middle", margin: [0.02, 0.06, 0.02, 0.06] });
});
{
  const s = content("Appendix: what we found in the originals", "X", "Sources: docs/SPEC.md section 3 and 6.2; build/audit/AUDIT.md; docs/baseline/baseline_tight.md; output/*/metrics.json (per-corner: timing__setup_vio__count__corner:max_ss_100C_1v60 = 32 for the original bnn_mnist, 1,306 for the original aes128).");
  const h2 = (t) => ({ text: t, options: { bold: true, color: C.WHITE, fill: { color: C.NAVY }, fontSize: 11, fontFace: FONT } });
  const c2 = (t, b) => ({ text: t, options: { fontSize: 10, fontFace: FONT, color: C.INK, bold: !!b, fill: { color: C.WHITE } } });
  const rows = [
    [h2("Design"), h2("Found"), h2("Now")],
    [c2("spm_ref", 1), c2("RTL did not multiply: 3 × 3 gave 3. Its test was failing but exited 0."), c2("Rewritten (carry-save, same ports); test checks 1,094,197 bits, 0 errors.")],
    [c2("counter_wb", 1), c2("Hollow: 1 of about 67 flip-flops survived synthesis (34 'multiple conflicting drivers'); RTL simulation, DRC, LVS and timing passed."), c2("All registers in one always block; gate-level test passes.")],
    [c2("ring_osc", 1), c2("Hollow: the 31-inverter ring was deleted by synthesis; the RTL test used a divider instead."), c2("Ring built from explicit sky130 cells; gate-level test on the real ring passes.")],
    [c2("aes128", 1), c2("Wrong ciphertext for both NIST vectors; 1,306 failing endpoints at max_ss_100C_1v60, worst setup −6.20 ns."), c2("Column-serial core passes FIPS-197 and 2,000 random blocks.")],
    [c2("bnn_mnist", 1), c2("Random weights; pixel register sampled every clock; testbench used the wrong address and passed anyway; 32 failing endpoints at max_ss_100C_1v60, worst setup −6.62 ns."), c2("Trained (90.10%); handshake fixed; RTL matches reference on 10,000 images.")],
    [c2("fcnn_mnist", 1), c2("Testbench only checked that the RTL compiles; accuracy in hardware unknown; 102,091 cells."), c2("196 inputs, 8-bit; 91.72% hardware-exact.")],
    [c2("cnn_fp16", 1), c2("Fails slow-corner setup (−5.18 ns) and hold (−0.49 ns); 142,619 max-slew violations."), c2("Not changed: HLS source unavailable; workstation-only.")],
    [c2("Six small tests", 1), c2("Each ended in a plain $finish, so a failed check could not fail the run."), c2("All testbenches now exit non-zero on a failed check or a timeout.")],
  ];
  s.addTable(rows, { x: 0.5, y: 1.0, w: 9.0, colW: [1.35, 4.4, 3.25], border: { type: "solid", pt: 0.75, color: C.LINE }, rowH: [0.28, 0.42, 0.5, 0.42, 0.5, 0.58, 0.42, 0.42, 0.42], valign: "middle", margin: [0.02, 0.08, 0.02, 0.08] });
}
{
  const u = statsFor("user_proj_example"), f = statsFor("fcnn_mnist");
  const s = content("Appendix: fcnn_mnist and user_proj_example", "X", "Sources: output/fcnn_mnist and output/user_proj_example metrics.json and resources.json; designs/user_proj_example/config.json RT_MAX_LAYER met4.");
  const rows = [["Design", "Std cells", "Setup / hold (ns)", "Wall", "Peak", "Max-slew", "Max-cap"].map(hd)];
  [["fcnn_mnist", f], ["user_proj_example (met4 only)", u]].forEach(([n, x]) => rows.push([cl(n, { bold: true, align: "left" })].concat(x ? [cl(x.cells), cl(`${sg(x.setup)} / ${sg(x.hold)}`), cl(wallFmt(x.wallS)), cl(x.peak.toFixed(2) + " GB"), cl(x.slew), cl(x.cap)] : Array(6).fill(pe("pending")))));
  s.addTable(rows, { x: 0.5, y: 1.5, w: 9.0, colW: [2.6, 1.0, 1.5, 0.9, 1.0, 1.0, 1.0], border: { type: "solid", pt: 0.5, color: C.LINE }, rowH: 0.4, valign: "middle" });
  T(s, "Same RTL; user_proj_example is routed up to met4 only.", 0.5, 3.4, 9, 0.4, { fontSize: 16, color: C.MUTED });
}
{
  const s = content("Appendix: lab and check commands", "X", "Sources: docs/LABS.md Commands; mk/L1.mk (lab-setup, lab, lab-check, lab-reset, lab-list, lab-knob, lab-pack, lab-unpack); mk/checks.mk (gl-<name>, gl-all, check-<name>, check-all). Options DESIGN=uart (default), PROFILE=tight, CPUSET, USE_DOCKER=0 (not tested), LABROOT.");
  const cmds = [["make lunch [DESIGN=bnn_mnist]", "one unattended run: check, install, simulate, harden, gl, pack"], ["make view [DESIGN=uart]", "walk through the stored results"], ["make lab-setup", "once: reference run + checkpoints (~2 min)"], ["make lab N=3", "run lab 3 into your own area"], ["make lab-check N=3", "compare with the reference"], ["make lab-list", "labs, expected time, your status"], ["make lab-reset", "clear your results"], ["make lab-knob KNOB=clock VALUE=10", "cause-and-effect experiment"], ["make lab-pack / lab-unpack PACK=<file>", "fallback pack"], ["make gl-<name> / check-<name>", "gate-level sim / surviving registers; <name> is spm, counter, uart, aes128, ring-osc, pwm-dac, sigma-delta, bnn, fcnn, user-proj or cnn-lite"]];
  const rows = [[hd("Command"), hd("What it does")]].concat(cmds.map((c) => [cl(c[0], { fontFace: MONO, align: "left", bold: true }), cl(c[1], { align: "left" })]));
  s.addTable(rows, { x: 0.5, y: 1.1, w: 9.0, colW: [4.6, 4.4], border: { type: "solid", pt: 0.5, color: C.LINE }, rowH: 0.34, valign: "middle", margin: [0.02, 0.08, 0.02, 0.08] });
}
{
  const s = content("Appendix: the stages of cnn_lite, step by step", "X", "Sources: build/results/_flow_stages/cnn_lite/stages.json (stage, step_directory, tool, step_wall_s, description). Pre-final images are drawn from DEF + LEF (cell outlines); the clock-tree image draws clock nets as straight lines.");
  const rows = [[hd("Stage"), hd("Step"), hd("Tool"), hd("Step time")]];
  (stageMeta && stageMeta.images ? stageMeta.images : []).forEach((i) => rows.push([cl(i.stage, { bold: true, align: "left" }), cl(i.step_directory.replace(/ \(.*$/, ""), { align: "left" }), cl(i.tool, { align: "left" }), cl(i.step_wall_s === null ? "n/a" : i.step_wall_s + " s")]));
  s.addTable(rows, { x: 0.5, y: 1.0, w: 9.0, colW: [1.6, 3.2, 3.0, 1.2], border: { type: "solid", pt: 0.5, color: C.LINE }, rowH: 0.3, valign: "middle", margin: [0.02, 0.06, 0.02, 0.06] });
}

// ================================================================ speaker notes with planned minutes
(function plan() {
  for (const b of BLOCKS) {
    const items = REG.filter((r) => r.block === b.id);
    const tot = items.reduce((a, r) => a + r.weight, 0) || 1;
    for (const r of items) {
      let head;
      if (b.id === "X") head = "PLAN: appendix, not presented.";
      else { const m = Math.max(0.5, Math.round(2 * (b.to - b.from) * r.weight / tot) / 2); head = `PLAN: block ${b.range} min (${b.name}); about ${m} min on this slide.`; }
      r.slide.addNotes(head + "\n" + (r.notes || ""));
    }
  }
})();

(async () => {
  await pres.writeFile({ fileName: OUT });
  const hook = SKILL_THEME || process.env.APPLY_THEME_JS;
  if (hook) { const { applyTheme } = require(hook); await applyTheme(OUT, THEME); console.log("theme applied"); }
  else console.log("NOTE: apply_theme.js not given (PPTX_APPLY_THEME); theme colours left at the default");
  console.log("wrote", OUT, "slides:", slideNo);
})();
