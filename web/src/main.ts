// ビュー 1 の画面：操作欄、周の一覧、グラフ。状態が変わるたびに全体を描き直す。

import "./style.css";
import { attachDistance, attachMap, drawDots, gAt, Link, type DistanceId } from "./brush";
import { renderCorner } from "./corner";
import { hasOtherBest, lossColor, miniLosses, renderPanel, sectionLosses, setupPanel } from "./sections";
import {
  courseMap,
  distanceChart,
  gDomain,
  ggChart,
  lapColors,
  lapStyle,
  PAIR_COLORS,
  type Chart,
  type LapStyle,
  type Paint,
} from "./charts";
import {
  defaultComparison,
  defaultSelection,
  finalDelta,
  loadCourse,
  loadIndex,
  refCandidates,
  shownLaps,
  timeDelta,
  type CourseData,
  type Lap,
  type Series,
} from "./data";

function el<T extends HTMLElement>(id: string): T {
  const e = document.getElementById(id);
  if (!e) throw new Error(`#${id} がない`);
  return e as T;
}

const ui = {
  course: el<HTMLSelectElement>("course"),
  showInvalid: el<HTMLInputElement>("show-invalid"),
  ref: el<HTMLSelectElement>("ref"),
  cmp: el<HTMLSelectElement>("cmp"),
  cmpLabel: el<HTMLLabelElement>("cmp-label"),
  multi: el<HTMLInputElement>("multi"),
  clear: el<HTMLButtonElement>("clear"),
  error: el<HTMLDivElement>("error"),
  attribution: el<HTMLParagraphElement>("attribution"),
  map: el<HTMLDivElement>("map"),
  readout: el<HTMLDivElement>("readout"),
  legend: el<HTMLDivElement>("legend"),
  speed: el<HTMLDivElement>("speed"),
  dt: el<HTMLDivElement>("dt"),
  ax: el<HTMLDivElement>("ax"),
  ay: el<HTMLDivElement>("ay"),
  gg: el<HTMLDivElement>("gg"),
  gScroll: el<HTMLDivElement>("g-scroll"),
  tabs: [...document.querySelectorAll<HTMLAnchorElement>(".tabs a")],
  cornerBar: el<HTMLDivElement>("corner-bar"),
  viewOverview: el<HTMLElement>("view-overview"),
  viewCorner: el<HTMLElement>("view-corner"),
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

interface State {
  doc: CourseData;
  colors: Map<string, string>;
  showInvalid: boolean;
  selected: Set<string>;
  /** false なら基準と比較の 2 周だけ描く（既定）。true なら一覧のチェックで選んだ周を重ねる */
  multi: boolean;
  /** 2 周で比べるときの比較の周 */
  cmp: string | null;
  ref: string | null;
  view: "overview" | "corner";
  /** 選んだコーナーの id。コーナーのビューで拡大するコーナー */
  corner: string | null;
}

/** 前後G・横G の距離のグラフの高さ [px] と、横の縮尺 [px/m]。全長を描き、一部だけ見せて横にスクロールさせる */
const G_HEIGHT = 170;
const G_PX_PER_M = 0.75;
/** コース図の高さの下限 [px]。これより低い画面ではページごとスクロールする */
const MAP_MIN_HEIGHT = 500;
/** コーナーの拡大図の幅の下限 [px] */
const CORNER_MAP_MIN_WIDTH = 320;

let state: State | null = null;
const link = new Link();
// コーナーのビューのグラフどうしの連動。全体のビューの選択とは分ける
const cornerLink = new Link();

/** 操作から呼ぶ描き直し。失敗は画面に出し、成功したら前の失敗の表示を消す。 */
function rerender(): void {
  try {
    render();
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

function fmt(v: number | null | undefined, digits: number, sign = false): string {
  if (v == null) return "N/A";
  const s = v.toFixed(digits);
  return sign && v > 0 ? `+${s}` : s;
}

function message(target: HTMLElement, text: string): void {
  const p = document.createElement("p");
  p.className = "message";
  p.textContent = text;
  target.replaceChildren(p);
}

function swatch(style: LapStyle): SVGSVGElement {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("width", "28");
  svg.setAttribute("height", "10");
  const line = document.createElementNS(ns, "line");
  for (const [k, v] of Object.entries({ x1: 1, x2: 27, y1: 5, y2: 5 })) line.setAttribute(k, String(v));
  line.setAttribute("stroke", style.stroke);
  line.setAttribute("stroke-width", String(style.strokeWidth + 0.8));
  if (style.strokeDasharray) line.setAttribute("stroke-dasharray", style.strokeDasharray);
  svg.append(line);
  return svg;
}

function hasValues(series: { values: Series }[]): boolean {
  return series.some(({ values }) => values.some((v) => v != null));
}

async function openCourse(course: string): Promise<void> {
  const doc = await loadCourse(course);
  state = {
    doc,
    colors: lapColors(doc.laps),
    showInvalid: ui.showInvalid.checked,
    selected: defaultSelection(doc.laps),
    multi: ui.multi.checked,
    cmp: null,
    ref: doc.default_ref,
    ...route(doc),
  };
  renderAttribution(doc);
  link.select(null);
  render();
  clearError();
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

function render(): void {
  if (!state) return;
  const { doc, colors, showInvalid, selected } = state;

  // 基準ラップ：一覧に出す周のうち complete の周から選ぶ。選んでいた周が候補から外れたら既定に戻す。
  // 「（選ぶ）」で外した（null の）ときは既定に戻さない
  const candidates = refCandidates(doc.laps, showInvalid);
  const has = (label: string | null) => candidates.some((l) => l.label === label);
  if (state.ref != null && !has(state.ref)) state.ref = has(doc.default_ref) ? doc.default_ref : null;
  const ref = doc.laps.find((l) => l.label === state!.ref) ?? null;
  ui.ref.replaceChildren(
    new Option("（選ぶ）", "", false, ref == null),
    ...candidates.map((l) => new Option(l.label, l.label, false, l === ref)),
  );

  const shown = shownLaps(doc.laps, showInvalid);
  // 比較の周：一覧に出す周のうち基準以外。選んでいた周が候補から外れたら既定に戻す
  const cmpCandidates = shown.filter((l) => l !== ref);
  if (!cmpCandidates.some((l) => l.label === state!.cmp)) {
    state.cmp = defaultComparison(cmpCandidates, ref)?.label ?? null;
  }
  const cmp = cmpCandidates.find((l) => l.label === state!.cmp) ?? null;
  ui.cmp.replaceChildren(
    new Option("（なし）", "", false, cmp == null),
    ...cmpCandidates.map((l) => new Option(l.label, l.label, false, l === cmp)),
  );
  ui.cmpLabel.hidden = state.multi;

  const overlaid = state.multi
    ? shown.filter((l) => selected.has(l.label))
    : [ref, cmp].filter((l): l is Lap => l != null);
  const lineColors = state.multi
    ? colors
    : new Map<string, string>([
        ...(ref ? [[ref.label, PAIR_COLORS.ref] as const] : []),
        ...(cmp ? [[cmp.label, PAIR_COLORS.cmp] as const] : []),
      ]);
  const style = (lap: Lap) => lapStyle(lap, lineColors, ref?.label ?? null);
  const dts = new Map<string, Series>(ref ? shown.map((l) => [l.label, timeDelta(l, ref)]) : []);

  renderTable(shown, style, dts);
  ui.legend.replaceChildren(
    ...overlaid.map((l) => {
      const item = document.createElement("span");
      item.append(swatch(style(l)), l.label);
      return item;
    }),
  );

  renderCornerBar(doc);
  ui.viewOverview.hidden = state.view !== "overview";
  ui.viewCorner.hidden = state.view !== "corner";
  for (const a of ui.tabs) {
    if (a.dataset.view === state.view) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
    // コーナーのタブは、今選んでいるコーナーを開く
    if (a.dataset.view === "corner" && state.corner) a.href = `#corner/${state.corner}`;
  }
  // 図を画面の高さに合わせる。見出しや操作の下から始まるので、その分を引き、開いた時点で図の全体が見えるようにする
  const mapHost = state.view === "corner" ? ui.corner.map : ui.map;
  const mapTop = mapHost.getBoundingClientRect().top + window.scrollY;
  const mapHeight = Math.max(MAP_MIN_HEIGHT, window.innerHeight - mapTop - 8);

  if (state.view === "corner") {
    ui.panel.root.hidden = true;
    link.reset();
    const corner = doc.corners?.find((c) => c.id === state!.corner);
    if (!corner) {
      message(ui.corner.map, "このコースには corners.toml がないので、コーナーのビューは出せない");
      return;
    }
    // 拡大図の幅は列の幅に合わせる。列の幅は CSS の flex で決まり、中の図の幅には引きずられない
    const mapWidth = Math.max(CORNER_MAP_MIN_WIDTH, ui.corner.map.clientWidth);
    renderCorner(ui.corner, doc, corner, overlaid, ref, style, swatch, cornerLink, mapWidth, mapHeight);
    return;
  }

  link.reset();
  // 区間ごとの損失：2 周で比べるときの比較の周について出す。複数の周を重ねるときは出さない
  const losses = !state.multi && cmp ? sectionLosses(doc, cmp) : null;
  ui.panel.root.hidden = losses == null;
  let paint: Paint | null = null;
  // 自己ベストを決める周がほかにない（損失がすべて 0）ときは塗らない
  if (losses && cmp && hasOtherBest(doc, cmp)) {
    const mini = miniLosses(doc, cmp);
    const color = lossColor(Math.max(0.1, ...mini.loss.map((v) => v ?? 0)));
    paint = { bounds: mini.bounds, color: mini.loss.map(color) };
  }
  const map = courseMap(doc, overlaid, style, mapHeight, paint);
  ui.map.replaceChildren(map.svg);
  attachMap(map, doc.refline, overlaid, (l) => style(l).stroke, link);

  const plain = (id: DistanceId, target: HTMLElement, values: (lap: Lap) => Series, yLabel: string, g: boolean) => {
    const series = overlaid.map((lap) => ({ lap, values: values(lap) }));
    if (!hasValues(series)) {
      message(target, "重ねる周を一覧で選ぶ");
      return null;
    }
    const chart = distanceChart(doc, series, style, {
      yLabel,
      zero: g,
      labelCorners: id === "speed" || id === "ax",
      height: g ? G_HEIGHT : undefined,
      width: g ? Math.round(doc.grid_max * G_PX_PER_M) + 70 : undefined,
    });
    target.replaceChildren(chart.svg);
    attachDistance(chart, id, link);
    return chart;
  };
  plain("speed", ui.speed, (l) => l.speed_kmh, "速度 [km/h]", false);
  const axChart = plain("ax", ui.ax, (l) => l.ax, "前後G ax [G]（加速が正）", true);
  plain("ay", ui.ay, (l) => l.ay, "横G ay [G]（左旋回が正）", true);

  const dtSeries = overlaid.map((lap) => ({ lap, values: dts.get(lap.label) ?? [] }));
  if (!ref) {
    message(
      ui.dt,
      has(doc.default_ref) ? "基準ラップを選ぶ" : "基準ラップを選ぶ（実車の有効で欠けのない周がないため、既定値がない）",
    );
  } else if (overlaid.length === 0) {
    message(ui.dt, "重ねる周を一覧で選ぶ");
  } else if (!hasValues(dtSeries)) {
    message(ui.dt, "重ねている周に complete の周がないため、Δt を出せない");
  } else {
    const label = `Δt [s]（基準：${ref.label}）`;
    const chart = distanceChart(doc, dtSeries, style, { yLabel: label, zero: true, labelCorners: false });
    ui.dt.replaceChildren(chart.svg);
    attachDistance(chart, "dt", link);
  }

  // ほかのグラフで区間を選んだら、G のグラフをその区間が真ん中に来るまで横に送る
  if (axChart) {
    link.add({
      onSelect: (sel) => {
        if (!sel || sel.source === "ax" || sel.source === "ay" || sel.source === "restored") return;
        const mid = axChart.x.apply((sel.range[0] + sel.range[1]) / 2);
        ui.gScroll.scrollTo({ left: mid - ui.gScroll.clientWidth / 2, behavior: "smooth" });
      },
      onHover: () => {},
    });
  }

  // G-G 図は選んだ区間の軌跡を濃く描くので、選択が変わるたびに描き直す。軸の範囲は周全体から決めて変えない
  if (overlaid.length === 0) {
    message(ui.gg, "重ねる周を一覧で選ぶ");
  } else {
    const d = gDomain(overlaid);
    let gg: Chart | null = null;
    let hovered: number | null = null;
    link.add({
      onSelect: (sel) => {
        gg = ggChart(overlaid, style, sel?.range ?? null, d);
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
    onHover: (s) => renderReadout(s, overlaid, style, dts),
  });
  if (losses && cmp) renderPanel(ui.panel.body, ui.panel.title, doc, cmp, losses, link);
}

/**
 * URL の hash から、どのビューのどのコーナーを出すか決める。`#overview`、`#corner`、`#corner/T3`。
 * ビューごとに URL を分け、ブラウザのタブを分けて開いたり、読み込み直しても同じビューに戻れるようにする。
 * 知らない hash や、そのコースにないコーナーは、全体のビューと最初のコーナーにする。
 */
function route(doc: CourseData): Pick<State, "view" | "corner"> {
  const [view, id] = window.location.hash.replace(/^#/, "").split("/");
  const corners = doc.corners ?? [];
  const corner = corners.find((c) => c.id === id)?.id ?? state?.corner ?? corners[0]?.id ?? null;
  return { view: view === "corner" ? "corner" : "overview", corner };
}

/** コーナーの記号のボタン（コーナーのビューだけ）。押すとそのコーナーの URL に移る。 */
function renderCornerBar(doc: CourseData): void {
  ui.cornerBar.replaceChildren(
    ...(doc.corners ?? []).map((c) => {
      const b = document.createElement("button");
      b.type = "button";
      b.textContent = c.id;
      b.title = `${c.name}（s = ${c.s_start}〜${c.s_end} m）`;
      b.setAttribute("aria-pressed", String(c.id === state?.corner));
      b.addEventListener("click", () => {
        window.location.hash = `corner/${c.id}`;
      });
      return b;
    }),
  );
}

function renderTable(shown: Lap[], style: (lap: Lap) => LapStyle, dts: Map<string, Series>): void {
  const head = ui.laps.createTHead();
  head.replaceChildren();
  const hr = head.insertRow();
  const first = state!.multi ? "重ねる" : "役割";
  for (const h of [first, "周", "source", "lap_valid", "complete", "t_end [s]", "最後の Δt [s]"]) {
    const th = document.createElement("th");
    th.textContent = h;
    hr.append(th);
  }
  const body = ui.laps.tBodies[0] ?? ui.laps.createTBody();
  body.replaceChildren();
  for (const lap of shown) {
    const tr = body.insertRow();
    if (lap.label === state?.ref) tr.className = "ref";
    if (state!.multi) {
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = state!.selected.has(lap.label);
      box.addEventListener("change", () => {
        if (box.checked) state!.selected.add(lap.label);
        else state!.selected.delete(lap.label);
        rerender();
      });
      tr.insertCell().append(box);
    } else {
      // 2 周で比べるときは、基準と比較の周を示す。ほかの周を押すと比較の周にする
      const role = lap.label === state!.ref ? "基準" : lap.label === state!.cmp ? "比較" : "";
      const cell = tr.insertCell();
      if (role) {
        cell.textContent = role;
      } else {
        const b = document.createElement("button");
        b.type = "button";
        b.textContent = "比較にする";
        b.addEventListener("click", () => {
          state!.cmp = lap.label;
          rerender();
        });
        cell.append(b);
      }
    }
    const name = document.createElement("span");
    name.className = "lap-label";
    name.append(swatch(style(lap)), lap.label);
    tr.insertCell().append(name);
    const dt = dts.get(lap.label);
    const last = dt ? finalDelta(dt) : null;
    const cells = [
      lap.source,
      String(lap.lap_valid),
      String(lap.complete),
      fmt(lap.t_end, 3),
      dt ? fmt(last?.dt, 3, true) : "—",
    ];
    for (const c of cells) tr.insertCell().textContent = c;
  }
}

function renderReadout(
  s: number | null,
  laps: Lap[],
  style: (lap: Lap) => LapStyle,
  dts: Map<string, Series>,
): void {
  if (s == null) {
    ui.readout.replaceChildren();
    return;
  }
  const head = document.createElement("div");
  head.textContent = `s = ${s} m`;
  const rows = laps.map((lap) => {
    const row = document.createElement("div");
    const dt = dts.get(lap.label)?.[s];
    row.append(
      swatch(style(lap)),
      `${fmt(lap.speed_kmh[s], 1)} km/h　Δt ${fmt(dt, 3, true)} s　ax ${fmt(lap.ax[s], 2, true)}　ay ${fmt(lap.ay[s], 2, true)} G`,
    );
    row.title = lap.label;
    return row;
  });
  ui.readout.replaceChildren(head, ...rows);
}

async function main(): Promise<void> {
  const courses = await loadIndex();
  if (courses.length === 0) throw new Error("data/index.json にコースがない。npm run data で書き出す");
  ui.course.replaceChildren(...courses.map((c) => new Option(c, c)));
  ui.course.addEventListener("change", () =>
    openCourse(ui.course.value).catch((e) => {
      // 読めなかったコースを選んだままにせず、画面に出ているコースに戻す
      if (state) ui.course.value = state.doc.course;
      showError(e);
    }),
  );
  ui.showInvalid.addEventListener("change", () => {
    if (!state) return;
    state.showInvalid = ui.showInvalid.checked;
    rerender();
  });
  ui.cmp.addEventListener("change", () => {
    if (!state) return;
    state.cmp = ui.cmp.value || null;
    rerender();
  });
  ui.multi.addEventListener("change", () => {
    if (!state) return;
    state.multi = ui.multi.checked;
    rerender();
  });
  ui.ref.addEventListener("change", () => {
    if (!state) return;
    state.ref = ui.ref.value || null;
    rerender();
  });
  ui.clear.addEventListener("click", () => {
    link.select(null);
    cornerLink.select(null);
  });
  window.addEventListener("hashchange", () => {
    if (!state) return;
    Object.assign(state, route(state.doc));
    rerender();
  });
  // コース図の高さを画面に合わせ直す。リサイズ中に何度も描き直さないよう、止まってから描く
  let resizeTimer: number | undefined;
  window.addEventListener("resize", () => {
    window.clearTimeout(resizeTimer);
    resizeTimer = window.setTimeout(rerender, 200);
  });
  setupPanel(ui.panel.root, ui.panel.toggle);
  await openCourse(courses[0]!);
}

main().catch(showError);
