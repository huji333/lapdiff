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
// - 表：区間タイム（section_times の値。基準ラップとの差）、始点・ボトム・終点の速度、ボトムの位置、ブレーキ開始点、最大減速度。
//   値は analysis.export が出す。
// - 距離のグラフにカーソルを置くと、拡大図と G-G 図にその s の各周の点を打つ。

import * as Plot from "@observablehq/plot";
import { attachDistance, drawDots, gAt, Link, xyAt } from "./brush";
import { cornerMap, distanceChart, gDomain, ggChart, type Chart, type LapStyle } from "./charts";
import {
  cornerWindow,
  hasPedals,
  pedalSeries,
  sectionTime,
  type Corner,
  type CourseData,
  type Lap,
  type Range,
} from "./data";

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

function fmt(v: number | null | undefined, digits: number, sign = false): string {
  if (v == null) return "N/A";
  const s = v.toFixed(digits);
  return sign && v > 0 ? `+${s}` : s;
}

export function renderCorner(
  els: CornerEls,
  doc: CourseData,
  corner: Corner,
  laps: Lap[],
  ref: Lap | null,
  style: (lap: Lap) => LapStyle,
  swatch: (style: LapStyle) => SVGSVGElement,
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
  els.title.textContent =
    `${corner.id} ${corner.name}　区間 s = ${corner.s_start}〜${corner.s_end} m（前後 ${PAD} m を含めて描く）`;

  link.reset();
  if (laps.length === 0) {
    for (const e of [els.map, els.offset, els.speed, els.ax, els.pedal, els.gg]) e.replaceChildren();
    els.map.textContent = "重ねる周を一覧で選ぶ";
    els.table.replaceChildren();
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
      yLabel:
        side == null
          ? "横のずれ [m]（基準線から左が正）"
          : `横のずれ [m]（${corner.id} の内側＝${side === "left" ? "左" : "右"}が正）`,
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
    yLabel: `前後G ax [G]（破線は -${doc.brake_g} G）`,
    extra: [...brakeRules, Plot.ruleY([-doc.brake_g], { stroke: "#999", strokeDasharray: "2,3" })],
  });
  els.ax.replaceChildren(ax.svg);
  attachDistance(ax, "ax", link);

  const pedalLaps = laps.filter(hasPedals);
  if (pedalLaps.length === 0) {
    const p = document.createElement("p");
    p.className = "message";
    p.textContent = "ペダルのグラフは、ペダルを記録している周（AC）を重ねると出る";
    els.pedal.replaceChildren(p);
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

  renderTable(els.table, doc, laps, ref, corner, style, swatch);
}

function renderTable(
  table: HTMLTableElement,
  doc: CourseData,
  laps: Lap[],
  ref: Lap | null,
  corner: Corner,
  style: (lap: Lap) => LapStyle,
  swatch: (style: LapStyle) => SVGSVGElement,
): void {
  const refT = ref ? sectionTime(doc, ref, corner.id) : null;
  const head = table.createTHead();
  head.replaceChildren();
  const hr = head.insertRow();
  const cols = [
    "周",
    "区間タイム [s]",
    "基準との差 [s]",
    "ブレーキ開始の s [m]",
    "最大減速度 [G]",
    "始点 [km/h]",
    "ボトム [km/h]",
    "ボトムの s [m]",
    "終点 [km/h]",
  ];
  for (const h of cols) {
    const th = document.createElement("th");
    th.textContent = h;
    hr.append(th);
  }
  const body = table.tBodies[0] ?? table.createTBody();
  body.replaceChildren();
  for (const lap of laps) {
    const st = lap.corner_stats?.[corner.id];
    const t = sectionTime(doc, lap, corner.id);
    const tr = body.insertRow();
    if (lap === ref) tr.className = "ref";
    const name = document.createElement("span");
    name.className = "lap-label";
    name.append(swatch(style(lap)), lap.label);
    tr.insertCell().append(name);
    const diff = t != null && refT != null ? t - refT : null;
    for (const c of [
      fmt(t, 3),
      fmt(diff, 3, true),
      // ax_min があって s_brake がないのは、しきい値を下回らなかった周
      st?.s_brake != null ? String(st.s_brake) : st?.ax_min != null ? "なし" : "N/A",
      fmt(st?.ax_min, 2),
      fmt(st?.v_entry, 1),
      fmt(st?.v_min, 1),
      st?.s_vmin == null ? "N/A" : String(st.s_vmin),
      fmt(st?.v_exit, 1),
    ]) {
      tr.insertCell().textContent = c;
    }
  }
}
