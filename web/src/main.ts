// ビュー 1 の画面：状態と配線。状態（derive.ts の State）が操作欄の値の唯一の正で、変えるのは update だけ。
// 描くときは derive で状態から値を求め、操作欄（renderToolbar）とグラフ（renderCharts）を描く。どちらも状態を変えない。

import "./style.css";
import { attachDistance, attachMap, drawDots, gAt, Link } from "./brush";
import { renderCorner } from "./corner";
import { lossColor, lossLegend, miniLosses, renderPanel, setupPanel } from "./sections";
import { courseMap, distanceChart, gDomain, ggChart, type Chart, type LapStyle, type Paint } from "./charts";
import { cornersOf, finalDelta, loadCourse, loadIndex, type CourseData, type Lap, type Series } from "./data";
import { cornerLoss, derive, initialState, normalize, type Derived, type State } from "./derive";
import { FRAGMENT, fmt, lapLabel, lapName, message, PICK_LAPS, setHead, swatch } from "./dom";

function el<T extends HTMLElement>(id: string): T {
  const e = document.getElementById(id);
  if (!e) throw new Error(`#${id} がない`);
  return e as T;
}

const ui = {
  course: el<HTMLSelectElement>("course"),
  courseLabel: el<HTMLLabelElement>("course-label"),
  showInvalid: el<HTMLInputElement>("show-invalid"),
  ref: el<HTMLSelectElement>("ref"),
  cmp: el<HTMLSelectElement>("cmp"),
  cmpLabel: el<HTMLLabelElement>("cmp-label"),
  multi: el<HTMLInputElement>("multi"),
  error: el<HTMLDivElement>("error"),
  attribution: el<HTMLParagraphElement>("attribution"),
  map: el<HTMLDivElement>("map"),
  mapLegend: el<HTMLDivElement>("map-legend"),
  readout: el<HTMLDivElement>("readout"),
  legend: el<HTMLDivElement>("legend"),
  speed: el<HTMLDivElement>("speed"),
  dt: el<HTMLDivElement>("dt"),
  gg: el<HTMLDivElement>("gg"),
  tabs: [...document.querySelectorAll<HTMLAnchorElement>(".tabs a")],
  cornerBar: el<HTMLDivElement>("corner-bar"),
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
/** コーナーの拡大図の幅の下限 [px] */
const CORNER_MAP_MIN_WIDTH = 320;

let courses: string[] = [];
let doc: CourseData | null = null;
let state: State | null = null;
const link = new Link();
// コーナーのビューのグラフどうしの連動。全体のビューの選択とは分ける
const cornerLink = new Link();

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

/** 操作欄（select、チェックボックス、タブ、コーナーのボタン、周の一覧）を state から書く */
function renderToolbar(state: State, doc: CourseData, d: Derived): void {
  ui.course.value = doc.course;
  ui.courseLabel.hidden = courses.length <= 1;
  ui.ref.replaceChildren(
    new Option("（選ぶ）", "", false, d.ref == null),
    ...d.refCandidates.map((l) => new Option(lapLabel(l), l.label, false, l === d.ref)),
  );
  ui.cmp.replaceChildren(
    new Option("（なし）", "", false, d.cmp == null),
    ...d.cmpCandidates.map((l) => new Option(lapLabel(l), l.label, false, l === d.cmp)),
  );
  ui.cmpLabel.hidden = state.multi;
  ui.multi.checked = state.multi;
  ui.showInvalid.checked = state.showInvalid;

  ui.viewOverview.hidden = state.view !== "overview";
  ui.viewCorner.hidden = state.view !== "corner";
  for (const a of ui.tabs) {
    if (a.dataset.view === state.view) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
    // コーナーのタブは、今選んでいるコーナーを開く
    if (a.dataset.view === "corner" && state.corner) a.href = `#corner/${state.corner}`;
  }
  renderCornerBar(state, doc, d);
  renderTable(state, d);
}

/** 重ねる周の凡例（線の見本と周の名前）。全体とコーナーの両方のビューで使う */
function renderLegend(target: HTMLElement, d: Derived): void {
  target.replaceChildren(...d.overlaid.map((l) => lapName(l, d.style(l))));
}

function renderCharts(state: State, doc: CourseData, d: Derived): void {
  const { overlaid, style } = d;
  // 図を画面の高さに合わせる。見出しや操作の下から始まるので、その分を引き、開いた時点で図の全体が見えるようにする
  const mapHost = state.view === "corner" ? ui.corner.map : ui.map;
  const mapTop = mapHost.getBoundingClientRect().top + window.scrollY;
  const mapHeight = Math.max(MAP_MIN_HEIGHT, window.innerHeight - mapTop - 8);

  if (state.view === "corner") {
    ui.panel.root.hidden = true;
    link.reset();
    const corner = cornersOf(doc).find((c) => c.id === state.corner);
    if (!corner || !d.corner) {
      message(ui.corner.map, "このコースには corners.toml がないので、コーナーのビューは出せない");
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
 * コーナーの記号のボタン（コーナーのビューだけ）。押すとそのコーナーの URL に移る。
 * 2 周で比べるときは、比較の周の損失（自己ベストとの差）を添える。`min_diff_s` 未満は灰色
 */
function renderCornerBar(state: State, doc: CourseData, d: Derived): void {
  ui.cornerBar.replaceChildren(
    ...cornersOf(doc).map((c) => {
      const b = document.createElement("button");
      b.type = "button";
      b.setAttribute("aria-pressed", String(c.id === state.corner));
      const loss = cornerLoss(d.losses, c.id);
      const titles = [`${c.name}（s = ${c.s_start}〜${c.s_end} m）`];
      b.append(c.id);
      if (loss != null && d.losses) {
        const v = document.createElement("span");
        v.className = loss < doc.min_diff_s ? "loss small" : "loss";
        v.textContent = fmt(loss, 2, true);
        b.append(" ", v);
        titles.push(`自己ベストとの差 ${fmt(loss, 3, true)} s（${lapLabel(d.losses.lap)}）`);
      }
      b.title = titles.join("　");
      b.addEventListener("click", () => {
        window.location.hash = `corner/${c.id}`;
      });
      return b;
    }),
  );
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
    if (lap.complete) {
      time.textContent = fmt(lap.t_end, 3);
    } else {
      time.textContent = FRAGMENT;
      time.title = `t_end ${fmt(lap.t_end, 3)} s`;
    }
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
  ui.course.replaceChildren(...courses.map((c) => new Option(c, c)));
  ui.course.addEventListener("change", () =>
    openCourse(ui.course.value).catch((e) => {
      // 読めなかったコースを選んだままにせず、画面に出ているコースに戻す
      if (doc) ui.course.value = doc.course;
      showError(e);
    }),
  );
  ui.showInvalid.addEventListener("change", () => update({ showInvalid: ui.showInvalid.checked }));
  ui.cmp.addEventListener("change", () => update({ cmp: ui.cmp.value || null }));
  ui.multi.addEventListener("change", () => update({ multi: ui.multi.checked }));
  ui.ref.addEventListener("change", () => update({ ref: ui.ref.value || null }));
  // 区間の選択はグラフのクリックかダブルクリックで解除する。Esc でも解除できるようにする
  window.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    link.select(null);
    cornerLink.select(null);
  });
  window.addEventListener("hashchange", () => {
    if (doc && state) update(route(doc, state.corner));
  });
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
