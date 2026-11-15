// ビュー 1 の画面：状態と配線。状態（derive.ts の State）が操作欄の値の唯一の正で、変えるのは update だけ。
// 描くときは derive で状態から値を求め、操作欄（renderToolbar）とグラフ（renderCharts）を描く。どちらも状態を変えない。
// 操作欄は sticky のツールバー：ビューのタブ、コース、基準 vs 比較のチップ（押すと周を選ぶポップオーバー）、
// 2 周 / 重ねる、選んだ区間のチップ。コーナーのビューでは 2 段目にコーナーのチップを並べる。

import "./style.css";
import { attachDistance, attachMap, drawDots, gAt, Link } from "./brush";
import { clearCorner, renderCorner } from "./corner";
import { LOSS_RAMP, lossColor, lossLegend, miniLosses, renderPanel, setupPanel } from "./sections";
import {
  courseMap,
  distanceChart,
  gDomain,
  ggChart,
  PAIR_COLORS,
  SELECT_COLOR,
  type Chart,
  type LapStyle,
  type Paint,
} from "./charts";
import { cornersOf, finalDelta, loadCourse, loadIndex, type CourseData, type Lap, type Series } from "./data";
import { cornerLoss, derive, initialState, normalize, type Derived, type State } from "./derive";
import { FRAGMENT, fmt, lapLabel, lapName, lapTime, message, PICK_LAPS, setHead, swatch } from "./dom";

function el<T extends HTMLElement>(id: string): T {
  const e = document.getElementById(id);
  if (!e) throw new Error(`#${id} がない`);
  return e as T;
}

type Role = "ref" | "cmp";

const ui = {
  header: el<HTMLElement>("toolbar"),
  course: el<HTMLSelectElement>("course"),
  chips: { ref: el<HTMLButtonElement>("ref-chip"), cmp: el<HTMLButtonElement>("cmp-chip") },
  pickers: { ref: el<HTMLElement>("ref-picker"), cmp: el<HTMLElement>("cmp-picker") },
  vs: el<HTMLElement>("vs"),
  mode: [...document.querySelectorAll<HTMLButtonElement>("#mode button")],
  selChip: el<HTMLButtonElement>("sel-chip"),
  error: el<HTMLDivElement>("error"),
  attribution: el<HTMLParagraphElement>("attribution"),
  lapsSection: el<HTMLElement>("laps-section"),
  lapsHost: { overview: el<HTMLElement>("dist-main"), corner: el<HTMLElement>("c-dist-col") },
  map: el<HTMLDivElement>("map"),
  mapLegend: el<HTMLDivElement>("map-legend"),
  readout: el<HTMLDivElement>("readout"),
  legend: el<HTMLDivElement>("legend"),
  speed: el<HTMLDivElement>("speed"),
  dt: el<HTMLDivElement>("dt"),
  gg: el<HTMLDivElement>("gg"),
  tabs: [...document.querySelectorAll<HTMLAnchorElement>(".tabs a")],
  cornerRow: el<HTMLDivElement>("corner-row"),
  cornerBar: el<HTMLDivElement>("corner-bar"),
  cornerPrev: el<HTMLButtonElement>("corner-prev"),
  cornerNext: el<HTMLButtonElement>("corner-next"),
  viewOverview: el<HTMLElement>("view-overview"),
  viewCorner: el<HTMLElement>("view-corner"),
  cornerLegend: el<HTMLDivElement>("c-legend"),
  corner: {
    title: el<HTMLElement>("c-title"),
    map: el<HTMLDivElement>("c-map"),
    offset: el<HTMLDivElement>("c-offset"),
    speed: el<HTMLDivElement>("c-speed"),
    ax: el<HTMLDivElement>("c-ax"),
    pedal: el<HTMLDivElement>("c-pedal"),
    gg: el<HTMLDivElement>("c-gg"),
    table: el<HTMLTableElement>("c-table"),
  },
  laps: el<HTMLTableElement>("laps"),
  panel: {
    root: el<HTMLElement>("sections-panel"),
    title: el<HTMLElement>("sp-title"),
    toggle: el<HTMLButtonElement>("sp-toggle"),
    body: el<HTMLElement>("sp-body"),
  },
};

/** コース図の高さの下限 [px]。これより低い画面ではページごとスクロールする */
const MAP_MIN_HEIGHT = 500;
/** コース図の下に空ける幅 [px]。style.css の .page の下の padding と同じにし、最下部までスクロールしても図の列が押し上げられないようにする */
const MAP_BOTTOM_GAP = 16;

/**
 * 線と損失の色の正は TS の定数（charts.ts、sections.ts）。SVG に塗る値と同じ色を CSS（基準の行の背景、
 * 選んだ区間のチップ、損失の棒と点）でも使えるよう、:root の変数に書く。
 */
const COLOR_TOKENS: Record<string, string> = {
  "--ref": PAIR_COLORS.ref,
  "--select": SELECT_COLOR,
  "--loss": LOSS_RAMP[1],
};
/** コーナーの拡大図の幅の下限 [px] */
const CORNER_MAP_MIN_WIDTH = 320;

let courses: string[] = [];
let doc: CourseData | null = null;
let state: State | null = null;
const link = new Link(() => renderSelChip());
// コーナーのビューのグラフどうしの連動。全体のビューの選択とは分ける
const cornerLink = new Link(() => renderSelChip());

/** 今のビューの選択の連動 */
function activeLink(): Link {
  return state?.view === "corner" ? cornerLink : link;
}

/** コーナーのビューの URL の hash。コーナーがなければビューだけ */
function cornerHash(id: string | null): string {
  return id ? `#corner/${id}` : "#corner";
}

/**
 * 状態を変える唯一の入口。基準と比較の周を候補に戻して（normalize）から描き直す。
 */
function update(patch: Partial<State>): void {
  if (!state || !doc) return;
  const next = { ...state, ...patch };
  state = { ...next, ...normalize(next, doc) };
  rerender();
}

/** 描き直し。`toolbar` が false なら操作欄はそのままで、グラフだけ描く（画面の大きさが変わったとき）。 */
function rerender(toolbar = true): void {
  if (!state || !doc) return;
  try {
    const d = derive(state, doc);
    if (toolbar) renderToolbar(state, doc, d);
    renderCharts(state, doc, d);
    clearError();
  } catch (e) {
    showError(e);
  }
}

function clearError(): void {
  ui.error.hidden = true;
  ui.error.textContent = "";
}

function showError(e: unknown): void {
  console.error(e);
  ui.error.hidden = false;
  ui.error.textContent = e instanceof Error ? e.message : String(e);
}

function hasValues(series: { values: Series }[]): boolean {
  return series.some(({ values }) => values.some((v) => v != null));
}

async function openCourse(course: string): Promise<void> {
  const next = await loadCourse(course);
  // 表示の切り替えは前のコースから引き継ぐ。コーナーは新しいコースで決め直す
  const keep = { showInvalid: state?.showInvalid ?? false, multi: state?.multi ?? false };
  doc = next;
  state = initialState(next, { ...keep, ...route(next, null) });
  renderAttribution(next);
  link.select(null);
  cornerLink.select(null);
  rerender();
}

/** 出典の表記（コースの端を空中写真から作ったとき）。表記は edges.csv の頭の行を書き出しがそのまま渡す */
function renderAttribution(doc: CourseData): void {
  ui.attribution.hidden = doc.edges == null;
  if (!doc.edges) return;
  const a = document.createElement("a");
  a.href = doc.edges.attribution_url;
  a.textContent = doc.edges.attribution;
  a.target = "_blank";
  a.rel = "noopener";
  ui.attribution.replaceChildren("コースの端：", a);
}

/** 操作欄（ツールバー、周を選ぶポップオーバー、コーナーのチップ、周の一覧）を state から書く */
function renderToolbar(state: State, doc: CourseData, d: Derived): void {
  ui.course.value = doc.course;
  ui.course.hidden = courses.length <= 1;
  renderChip("ref", d.ref, d);
  renderChip("cmp", d.cmp, d);
  ui.chips.cmp.hidden = state.multi;
  ui.vs.hidden = state.multi;
  renderPicker("ref", [null, ...d.refCandidates], d.ref, state);
  renderPicker("cmp", [null, ...d.cmpCandidates], d.cmp, state);
  for (const b of ui.mode) b.setAttribute("aria-pressed", String(b.dataset.multi === String(state.multi)));
  renderSelChip();

  ui.viewOverview.hidden = state.view !== "overview";
  ui.viewCorner.hidden = state.view !== "corner";
  // 周の一覧は今のビューの右の列の末尾に置く。ビューの外（ページの最下部）に置くと、最下部までスクロールしたとき
  // 貼り付いた図の列がビューの終わりに押し上げられ、上の端がツールバーの下に隠れる
  const host = ui.lapsHost[state.view];
  if (ui.lapsSection.parentElement !== host) host.append(ui.lapsSection);
  for (const a of ui.tabs) {
    if (a.dataset.view === state.view) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
    // コーナーのタブは、今選んでいるコーナーを開く
    if (a.dataset.view === "corner") a.href = cornerHash(state.corner);
  }
  ui.cornerRow.hidden = state.view !== "corner" || cornersOf(doc).length === 0;
  renderCornerBar(state, doc, d);
  renderTable(state, d);
}

const PICK_ROLE: Record<Role, { name: string; none: string; empty: string }> = {
  ref: { name: "基準ラップ", none: "（選ぶ）", empty: "基準を選ぶ" },
  cmp: { name: "比較", none: "（なし）", empty: "比較なし" },
};

/** 基準・比較のチップ：周の色の点、短い名前、タイム。周がなければ点線の枠 */
function renderChip(kind: Role, lap: Lap | null, d: Derived): void {
  const chip = ui.chips[kind];
  const role = PICK_ROLE[kind];
  chip.classList.toggle("ghost", lap == null);
  if (!lap) {
    chip.title = role.name;
    chip.replaceChildren(role.empty);
    return;
  }
  chip.title = `${role.name}：${lap.label}`;
  const dot = document.createElement("span");
  dot.className = "dot";
  dot.style.background = d.style(lap).stroke;
  const time = document.createElement("small");
  time.textContent = lapTime(lap);
  chip.replaceChildren(dot, lapLabel(lap), time);
}

/** 周を選ぶポップオーバーの中身。押すとその周にして閉じる。無効な周も出すかの切り替えはここに置く */
function renderPicker(kind: Role, laps: (Lap | null)[], current: Lap | null, state: State): void {
  const picker = ui.pickers[kind];
  for (const box of picker.querySelectorAll<HTMLInputElement>(".show-invalid")) box.checked = state.showInvalid;
  const list = picker.querySelector(".pick-list");
  if (!list) throw new Error(`#${picker.id} に .pick-list がない`);
  list.replaceChildren(
    ...laps.map((lap) => {
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("role", "option");
      b.setAttribute("aria-selected", String(lap === current));
      if (lap) {
        if (!lap.lap_valid) b.className = "invalid";
        b.title = lap.label;
        const time = document.createElement("small");
        time.textContent = lapTime(lap);
        b.append(lapLabel(lap), time);
      } else {
        b.append(PICK_ROLE[kind].none);
      }
      b.addEventListener("click", () => {
        picker.hidePopover();
        update({ [kind]: lap?.label ?? null });
      });
      return b;
    }),
  );
}

/**
 * 選んだ区間のチップ。今のビューで区間を選んでいるときだけ見せ、押すと解除する。
 * 隠すときも場所は残す（style.css の .off）ので、選択の出し入れでツールバーの高さも部品の位置も変わらない
 */
function renderSelChip(): void {
  const sel = activeLink().selection;
  ui.selChip.classList.toggle("off", sel == null);
  if (!sel) {
    ui.selChip.textContent = "";
    return;
  }
  const [a, b] = [...sel.range].sort((x, y) => x - y).map(Math.round);
  ui.selChip.textContent = `s ${a}–${b} m ✕`;
}

/** ツールバーの高さを CSS（貼り付ける列の top）に渡す。折り返し（画面の幅）やコーナーの段の出し入れで変わる */
function syncHeaderHeight(): void {
  document.documentElement.style.setProperty("--header-h", `${ui.header.offsetHeight}px`);
}

/** 重ねる周の凡例（線の見本と周の名前）。全体とコーナーの両方のビューで使う */
function renderLegend(target: HTMLElement, d: Derived): void {
  target.replaceChildren(...d.overlaid.map((l) => lapName(l, d.style(l))));
}

function renderCharts(state: State, doc: CourseData, d: Derived): void {
  const { overlaid, style } = d;
  // 図を画面の高さに合わせる。図の列はツールバーの下に貼り付く（CSS の top）ので、その分を引く
  syncHeaderHeight();
  const mapCol = (state.view === "corner" ? ui.corner.map : ui.map).parentElement!;
  const stickyTop = parseFloat(getComputedStyle(mapCol).top);
  if (!Number.isFinite(stickyTop)) throw new Error("図の列の top が px でない（style.css の sticky を確かめる）");
  const mapHeight = Math.max(MAP_MIN_HEIGHT, window.innerHeight - stickyTop - MAP_BOTTOM_GAP);

  if (state.view === "corner") {
    ui.panel.root.hidden = true;
    link.reset();
    const corner = cornersOf(doc).find((c) => c.id === state.corner);
    if (!corner || !d.corner) {
      cornerLink.reset();
      ui.corner.title.textContent = "";
      ui.cornerLegend.replaceChildren();
      clearCorner(ui.corner, "このコースには corners.toml がないので、コーナーのビューは出せない");
      return;
    }
    renderLegend(ui.cornerLegend, d);
    // 拡大図の幅は列の幅に合わせる。列の幅は CSS の flex で決まり、中の図の幅には引きずられない
    const mapWidth = Math.max(CORNER_MAP_MIN_WIDTH, ui.corner.map.clientWidth);
    renderCorner(ui.corner, doc, corner, overlaid, d.corner, style, cornerLink, mapWidth, mapHeight);
    return;
  }

  link.reset();
  if (overlaid.length === 0) message(ui.legend, PICK_LAPS);
  else renderLegend(ui.legend, d);
  const { losses } = d;
  ui.panel.root.hidden = losses == null;
  let paint: Paint | null = null;
  ui.mapLegend.replaceChildren();
  // 自己ベストを決める周がほかにない（損失がすべて 0）ときは塗らない
  if (losses?.comparable) {
    const mini = miniLosses(doc, losses.lap);
    const max = Math.max(0.1, ...mini.loss.map((v) => v ?? 0));
    const color = lossColor(max);
    paint = { bounds: mini.bounds, color: mini.loss.map(color) };
    ui.mapLegend.replaceChildren(lossLegend(max));
  }
  const map = courseMap(doc, overlaid, style, mapHeight, paint, !state.multi);
  ui.map.replaceChildren(map.svg);
  attachMap(map, doc.refline, overlaid, (l) => style(l).stroke, link);

  const speed = overlaid.map((lap) => ({ lap, values: lap.speed_kmh }));
  if (hasValues(speed)) {
    const chart = distanceChart(doc, speed, style, { yLabel: "速度 [km/h]", zero: false, labelCorners: true });
    ui.speed.replaceChildren(chart.svg);
    attachDistance(chart, "speed", link);
  } else {
    ui.speed.replaceChildren();
  }

  const dtSeries = overlaid.map((lap) => ({ lap, values: d.dts.get(lap.label) ?? [] }));
  if (!d.ref) {
    message(ui.dt, "基準ラップを選ぶ", d.hasDefaultRef ? undefined : "実車の有効で欠けのない周がないため、既定値がない");
  } else if (overlaid.length === 0) {
    ui.dt.replaceChildren();
  } else if (!hasValues(dtSeries)) {
    message(ui.dt, `Δt なし（重ねた周がすべて${FRAGMENT}）`);
  } else {
    const chart = distanceChart(doc, dtSeries, style, { yLabel: "Δt [s]（+ = 遅い）", zero: true, labelCorners: false });
    ui.dt.replaceChildren(chart.svg);
    attachDistance(chart, "dt", link);
  }

  // G-G 図は選んだ区間の軌跡を濃く描くので、選択が変わるたびに描き直す。軸の範囲は周全体から決めて変えない
  if (overlaid.length === 0) {
    ui.gg.replaceChildren();
  } else {
    const dom = gDomain(overlaid);
    let gg: Chart | null = null;
    let hovered: number | null = null;
    link.add({
      onSelect: (sel) => {
        gg = ggChart(overlaid, style, sel?.range ?? null, dom);
        ui.gg.replaceChildren(gg.svg);
        drawDots(gg, overlaid, (l) => style(l).stroke, hovered, gAt);
      },
      onHover: (s) => {
        hovered = s;
        if (gg) drawDots(gg, overlaid, (l) => style(l).stroke, s, gAt);
      },
    });
  }

  link.add({
    onSelect: () => {},
    onHover: (s) => renderReadout(s, overlaid, style, d.dts),
  });
  if (losses) renderPanel(ui.panel.body, ui.panel.title, doc, losses, link);
}

/**
 * URL の hash から、どのビューのどのコーナーを出すか決める。`#overview`、`#corner`、`#corner/T3`。
 * ビューごとに URL を分け、ブラウザのタブを分けて開いたり、読み込み直しても同じビューに戻れるようにする。
 * 知らない hash や、そのコースにないコーナーは、全体のビューと `prev`（なければ最初のコーナー）にする。
 */
function route(doc: CourseData, prev: string | null): Pick<State, "view" | "corner"> {
  const [view, id] = window.location.hash.replace(/^#/, "").split("/");
  const corners = cornersOf(doc);
  const known = (c: string | null | undefined) => corners.find((k) => k.id === c)?.id;
  const corner = known(id) ?? known(prev) ?? corners[0]?.id ?? null;
  return { view: view === "corner" ? "corner" : "overview", corner };
}

/**
 * コーナーのチップ（コーナーのビューのツールバーの 2 段目）。押すとそのコーナーの URL に移る。
 * 2 周で比べるときは、比較の周の損失（自己ベストとの差）を点の濃さと値で添える。`min_diff_s` 未満は薄く
 */
function renderCornerBar(state: State, doc: CourseData, d: Derived): void {
  const corners = cornersOf(doc);
  const losses = new Map(corners.map((c) => [c.id, cornerLoss(d.losses, c.id)]));
  const maxLoss = Math.max(doc.min_diff_s, ...[...losses.values()].map((v) => v ?? 0));
  ui.cornerBar.replaceChildren(
    ...corners.map((c) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "corner";
      b.setAttribute("aria-pressed", String(c.id === state.corner));
      const loss = losses.get(c.id) ?? null;
      const titles = [`${c.name}（s = ${c.s_start}〜${c.s_end} m）`];
      if (loss != null && d.losses) {
        const dot = document.createElement("span");
        dot.className = "dot";
        dot.style.setProperty("--l", String(Math.max(0, loss) / maxLoss));
        const v = document.createElement("span");
        v.className = loss < doc.min_diff_s ? "loss small" : "loss";
        v.textContent = fmt(loss, 2, true);
        b.append(dot, c.id, v);
        titles.push(`自己ベストとの差 ${fmt(loss, 3, true)} s（${lapLabel(d.losses.lap)}）`);
      } else {
        b.append(c.id);
      }
      b.title = titles.join("　");
      b.addEventListener("click", () => {
        window.location.hash = cornerHash(c.id);
      });
      return b;
    }),
  );
}

/** 前後のコーナーへ移る。端では反対の端へ回る（周回のコースなので） */
function stepCorner(step: 1 | -1): void {
  if (!doc || !state) return;
  const ids = cornersOf(doc).map((c) => c.id);
  if (ids.length === 0) return;
  const i = state.corner == null ? -1 : ids.indexOf(state.corner);
  window.location.hash = cornerHash(ids[(i + step + ids.length) % ids.length]!);
}

/** キー操作を受けないとき：入力の部品にフォーカスがある、修飾キー付き、周を選ぶポップオーバーが開いている */
function ignoreKey(e: KeyboardEvent): boolean {
  const t = e.target;
  const typing = t instanceof Element && t.closest("input, select, textarea, [contenteditable]") != null;
  return typing || e.metaKey || e.ctrlKey || e.altKey || document.querySelector(":popover-open") != null;
}

/** キー操作：1 / 2 でビュー、← / → でコーナー（コーナーのビュー）、Esc で選択を解除 */
function onKey(e: KeyboardEvent): void {
  if (ignoreKey(e) || !state) return;
  if (e.key === "Escape") {
    link.select(null);
    cornerLink.select(null);
  } else if (e.key === "1") {
    window.location.hash = "#overview";
  } else if (e.key === "2") {
    window.location.hash = cornerHash(state.corner);
  } else if (state.view === "corner" && (e.key === "ArrowLeft" || e.key === "ArrowRight")) {
    e.preventDefault();
    stepCorner(e.key === "ArrowLeft" ? -1 : 1);
  }
}

/** ポップオーバーをチップの下に、右端をそろえて出す */
function placePicker(kind: Role): void {
  const r = ui.chips[kind].getBoundingClientRect();
  const p = ui.pickers[kind];
  p.style.top = `${r.bottom + 6}px`;
  p.style.right = `${document.documentElement.clientWidth - r.right}px`;
}

function renderTable(state: State, d: Derived): void {
  setHead(ui.laps, ["", "周", "タイム [s]", "基準との差 [s]"]);
  const body = ui.laps.tBodies[0] ?? ui.laps.createTBody();
  body.replaceChildren();
  for (const lap of d.shown) {
    const tr = body.insertRow();
    const cls = [lap === d.ref ? "ref" : "", lap.lap_valid ? "" : "invalid"].filter(Boolean);
    if (cls.length) tr.className = cls.join(" ");
    if (state.multi) {
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = state.selected.has(lap.label);
      box.addEventListener("change", () => {
        const selected = new Set(state.selected);
        if (box.checked) selected.add(lap.label);
        else selected.delete(lap.label);
        update({ selected });
      });
      tr.insertCell().append(box);
    } else {
      // 2 周で比べるときは、基準と比較の周を示す。ほかの周を押すと比較の周にする
      const role = lap === d.ref ? "基準" : lap === d.cmp ? "比較" : "";
      const cell = tr.insertCell();
      if (role) {
        cell.textContent = role;
      } else {
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = "比較にする";
        b.addEventListener("click", () => update({ cmp: lap.label }));
        cell.append(b);
      }
    }
    tr.insertCell().append(lapName(lap, d.style(lap)));
    const time = tr.insertCell();
    time.textContent = lapTime(lap);
    if (!lap.complete) time.title = `t_end ${fmt(lap.t_end, 3)} s`;
    const diff = tr.insertCell();
    const dt = d.dts.get(lap.label);
    const last = dt ? finalDelta(dt) : null;
    diff.textContent = dt ? fmt(last?.dt, 3, true) : "—";
    if (dt && last == null && !lap.complete) diff.title = `${FRAGMENT}なので出さない`;
  }
}

function renderReadout(s: number | null, laps: Lap[], style: (lap: Lap) => LapStyle, dts: Map<string, Series>): void {
  if (s == null) {
    ui.readout.replaceChildren();
    return;
  }
  const head = document.createElement("div");
  head.textContent = `s = ${s} m　km/h　Δt [s]`;
  const rows = laps.map((lap) => {
    const row = document.createElement("div");
    row.append(swatch(style(lap)), `${fmt(lap.speed_kmh[s], 1)}　${fmt(dts.get(lap.label)?.[s], 3, true)}`);
    row.title = lap.label;
    return row;
  });
  ui.readout.replaceChildren(head, ...rows);
}

async function main(): Promise<void> {
  courses = await loadIndex();
  if (courses.length === 0) throw new Error("data/index.json にコースがない。npm run data で書き出す");
  for (const [k, v] of Object.entries(COLOR_TOKENS)) document.documentElement.style.setProperty(k, v);
  ui.course.replaceChildren(...courses.map((c) => new Option(c, c)));
  ui.course.addEventListener("change", () =>
    openCourse(ui.course.value).catch((e) => {
      // 読めなかったコースを選んだままにせず、画面に出ているコースに戻す
      if (doc) ui.course.value = doc.course;
      showError(e);
    }),
  );
  for (const kind of ["ref", "cmp"] as const) {
    const picker = ui.pickers[kind];
    picker.addEventListener("beforetoggle", (e) => {
      if ((e as ToggleEvent).newState === "open") placePicker(kind);
    });
    for (const box of picker.querySelectorAll<HTMLInputElement>(".show-invalid")) {
      box.addEventListener("change", () => update({ showInvalid: box.checked }));
    }
  }
  for (const b of ui.mode) b.addEventListener("click", () => update({ multi: b.dataset.multi === "true" }));
  ui.selChip.addEventListener("click", () => activeLink().select(null));
  ui.cornerPrev.addEventListener("click", () => stepCorner(-1));
  ui.cornerNext.addEventListener("click", () => stepCorner(1));
  // 区間の選択はグラフのクリックかダブルクリックでも解除できる
  window.addEventListener("keydown", onKey);
  window.addEventListener("hashchange", () => {
    if (doc && state) update(route(doc, state.corner));
  });
  // 描き直しを伴わずにツールバーの高さが変わったとき（書体の読み込みなど）も、貼り付ける列の top を追う。
  // コース図の高さは描き直しでしか合わせない。選択のチップは場所を取ったままなので、選択では高さが変わらない
  new ResizeObserver(syncHeaderHeight).observe(ui.header);
  // コース図の高さを画面に合わせ直す。リサイズ中に何度も描き直さないよう、止まってから描く
  let resizeTimer: number | undefined;
  window.addEventListener("resize", () => {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(() => rerender(false), 200);
  });
  setupPanel(ui.panel.root, ui.panel.toggle);
  await openCourse(courses[0]!);
}

main().catch(showError);
