// コーナーのビュー：1 つのコーナーの前後を拡大して、ライン取りを周どうしで比べる。
//
// - 拡大図：路面、基準線（細い破線）と各周の走行ライン。各周のボトム速度の位置に点と km/h を出す。
//   幅は列の幅に合わせる（呼ぶ側が測って渡す）。
// - 距離×横のずれと距離×速度を、コーナーの前後の範囲だけ描く。
//   横のずれで「どこで外に振り、どこで寄せたか」、速度で「その結果どこまで落ちたか」を同じ s で見る。
//   横のずれは、そのコーナーの内側（書き出しの side）を正にし、上ほどイン寄りと読めるようにする。
//   前後 PAD m に隣の逆向きのコーナーがかかっても、正の向きはこのコーナーの内側のまま変えない。軸の名前に左右を書く。
// - G-G 図：その範囲の軌跡を濃く描く（周全体は薄く）。
// - 距離×前後G：ブレーキ開始点のしきい値（brake_g）を破線で引く。各周のブレーキ開始点は、どの距離のグラフにも周の色の縦の破線で示す。
// - 距離×ペダル：アクセルを上、ブレーキを下に [%]。ペダルを記録している周（AC）だけ描く。
//   実車の前後G から決めたブレーキ開始点と、シムの実際の踏み始めを同じ s で比べられる。
// - 表：区間タイム（section_times の値）、ブレーキ開始点、最大減速度、始点・ボトム・終点の速度。
//   基準の行は値、ほかの行は基準との差（derive が求める）。値は analysis.export が出す。
// - 距離のグラフにカーソルを置くと、拡大図と G-G 図にその s の各周の点を打つ。

import * as Plot from "@observablehq/plot";
import { attachDistance, drawDots, gAt, Link, xyAt } from "./brush";
import { cornerMap, distanceChart, gDomain, ggChart, type Chart, type LapStyle } from "./charts";
import { cornerWindow, hasPedals, pedalSeries, type Corner, type CourseData, type Lap, type Range } from "./data";
import type { CornerRow, CornerTable, DiffKey } from "./derive";
import { fmt, lapName, message, PICK_LAPS, setHead, type Head } from "./dom";

/** コーナーの区間の前後に足して描く距離 [m] */
const PAD = 80;
const PLOT_WIDTH = 640;
const PLOT_HEIGHT = 190;

export interface CornerEls {
  title: HTMLElement;
  map: HTMLElement;
  offset: HTMLElement;
  speed: HTMLElement;
  ax: HTMLElement;
  pedal: HTMLElement;
  gg: HTMLElement;
  table: HTMLTableElement;
}

export function renderCorner(
  els: CornerEls,
  doc: CourseData,
  corner: Corner,
  laps: Lap[],
  table: CornerTable,
  style: (lap: Lap) => LapStyle,
  link: Link,
  mapWidth: number,
  mapHeight: number,
): void {
  const brakes = laps.flatMap((lap) => {
    const s = lap.corner_stats?.[corner.id]?.s_brake;
    return s == null ? [] : [{ lap, s }];
  });
  // ブレーキ開始点が区間の手前 PAD m より前にある周もあるので、そこまで広げる
  const base = cornerWindow(corner, doc.grid_max, PAD);
  const win: Range = [Math.max(0, Math.min(base[0], ...brakes.map((b) => b.s - 20))), base[1]];
  els.title.textContent = `${corner.id} ${corner.name}`;
  els.title.title = `区間 s = ${corner.s_start}〜${corner.s_end} m`;

  link.reset();
  if (laps.length === 0) {
    clearCorner(els, PICK_LAPS);
    return;
  }

  const color = (l: Lap) => style(l).stroke;
  const map = cornerMap(doc, laps, style, corner, win, mapWidth, mapHeight);
  els.map.replaceChildren(map.svg);

  const brakeRules = brakes.map((b) =>
    Plot.ruleX([b.s], { stroke: style(b.lap).stroke, strokeDasharray: "4,3", strokeOpacity: 0.8 }),
  );
  const opts = {
    zero: false,
    labelCorners: true,
    width: PLOT_WIDTH,
    height: PLOT_HEIGHT,
    xDomain: win,
    extra: brakeRules,
  };
  // offset は進む向きの左が正。右のコーナーでは符号を反転して内側を正にする。向きが決まらなければ左を正のまま描く
  const side = corner.side;
  const inward = side === "right" ? -1 : 1;
  const offset = distanceChart(
    doc,
    laps.map((lap) => ({ lap, values: lap.offset.map((v) => (v == null ? null : inward * v)) })),
    style,
    {
      ...opts,
      zero: true,
      yLabel: `横のずれ [m]（実車平均線から、↑ ${side == null ? "左" : `${corner.id} のイン`}）`,
    },
  );
  els.offset.replaceChildren(offset.svg);
  attachDistance(offset, "offset", link);
  const speed = distanceChart(doc, laps.map((lap) => ({ lap, values: lap.speed_kmh })), style, {
    ...opts,
    yLabel: "速度 [km/h]",
  });
  els.speed.replaceChildren(speed.svg);
  attachDistance(speed, "speed", link);
  const ax = distanceChart(doc, laps.map((lap) => ({ lap, values: lap.ax })), style, {
    ...opts,
    zero: true,
    yLabel: `前後G [G]（破線 −${doc.brake_g} G を下回る点＝ブレーキ開始）`,
    extra: [...brakeRules, Plot.ruleY([-doc.brake_g], { stroke: "#999", strokeDasharray: "2,3" })],
  });
  els.ax.replaceChildren(ax.svg);
  attachDistance(ax, "ax", link);

  const pedalLaps = laps.filter(hasPedals);
  if (pedalLaps.length === 0) {
    message(els.pedal, "ペダル：AC の周のみ");
  } else {
    const series = pedalLaps.flatMap((lap) => {
      const { throttle, brake } = pedalSeries(lap, win);
      return [
        { lap, values: throttle },
        { lap, values: brake },
      ];
    });
    const pedal = distanceChart(doc, series, style, {
      ...opts,
      zero: true,
      yLabel: "↑ アクセル [%]　ブレーキ [%] ↓",
    });
    els.pedal.replaceChildren(pedal.svg);
    attachDistance(pedal, "pedal", link);
  }

  // G-G 図は選んだ範囲（なければコーナーの前後）の軌跡を濃く描く
  const d = gDomain(laps);
  let gg: Chart | null = null;
  let hovered: number | null = null;
  link.add({
    onSelect: (sel) => {
      gg = ggChart(laps, style, sel?.range ?? win, d);
      els.gg.replaceChildren(gg.svg);
      drawDots(gg, laps, color, hovered, gAt);
    },
    onHover: (s) => {
      hovered = s;
      drawDots(map, laps, color, s, xyAt);
      if (gg) drawDots(gg, laps, color, s, gAt);
    },
  });

  renderTable(els.table, doc, laps, table, style);
}

/** コーナーの図と表を消し、拡大図の場所に `text` を出す。前に描いたコーナーを残さない */
export function clearCorner(els: CornerEls, text: string): void {
  for (const e of [els.map, els.offset, els.speed, els.ax, els.pedal, els.gg]) e.replaceChildren();
  els.table.replaceChildren();
  message(els.map, text);
}

/** 表の列。基準の行は値、ほかの行は基準との差（基準にその値がある列だけ） */
const COLS: { key: DiffKey; text: string; digits: number; sign: string }[] = [
  { key: "t", text: "区間タイム [s]", digits: 3, sign: "+ が遅い" },
  { key: "s_brake", text: "ブレーキ [m]", digits: 0, sign: "+ が奥" },
  { key: "ax_min", text: "最大減速度 [G]", digits: 2, sign: "− が強い" },
  { key: "v_entry", text: "始点 [km/h]", digits: 1, sign: "+ が速い" },
  { key: "v_min", text: "ボトム [km/h]", digits: 1, sign: "+ が速い" },
  { key: "v_exit", text: "終点 [km/h]", digits: 1, sign: "+ が速い" },
];

function renderTable(
  table: HTMLTableElement,
  doc: CourseData,
  laps: Lap[],
  { ref, rows }: CornerTable,
  style: (lap: Lap) => LapStyle,
): void {
  const diffCol = (key: DiffKey) => ref?.[key] != null;
  // 実車の周を重ねているときは、ブレーキ開始点がしきい値に敏感なことを見出しに出す
  const real = laps.some((l) => l.source === "racechrono");
  const brakeDef =
    `前後G が −${doc.brake_g} G を下回り始めた点（ペダルではない）。` +
    (real ? `実車はしきい値を 0.1 G 変えると 30 m 以上動くコーナーがある。` : "");
  const heads: Head[] = COLS.map(({ key, text, sign }) => {
    const diff = diffCol(key);
    const h: Head = { text: key === "s_brake" && real ? "ブレーキ~ [m]" : text };
    const titles = [key === "s_brake" ? brakeDef : "", diff ? `基準の行は値、ほかは基準との差（${sign}）` : ""];
    if (titles.some(Boolean)) h.title = titles.join("");
    return h;
  });
  setHead(table, ["周", ...heads]);
  const body = table.tBodies[0] ?? table.createTBody();
  body.replaceChildren();
  for (const row of rows) {
    const tr = body.insertRow();
    if (row.diff == null && ref != null) tr.className = "ref";
    tr.insertCell().append(lapName(row.lap, style(row.lap)));
    for (const col of COLS) {
      const td = tr.insertCell();
      td.textContent = cell(row, col.key, col.digits, diffCol(col.key));
      if (col.key === "t" && diffCol("t") && row.diff) td.title = `区間タイム ${fmt(row.values.t, 3)} s`;
      if (col.key === "v_min" && row.values.s_vmin != null) td.title = `s = ${row.values.s_vmin} m`;
    }
  }
}

function cell(row: CornerRow, key: DiffKey, digits: number, diffCol: boolean): string {
  const { values, diff } = row;
  // ax_min があって s_brake がないのは、しきい値を下回らなかった周
  if (key === "s_brake" && values.s_brake == null) return values.ax_min != null ? "なし" : "N/A";
  return diff && diffCol ? fmt(diff[key], digits, true) : fmt(values[key], digits);
}
