// 区間の連動とカーソル。Plot に区間選択はないので、d3-brush を Plot の SVG に重ね、
// Plot の縮尺（charts.ts の Chart）で画面座標と s を変換する。
//
// - 距離のグラフで横にドラッグ：その s の範囲を、コース図では基準線に沿った線で、ほかの距離のグラフでは帯で示す（SELECT_COLOR）。
// - コース図で矩形をドラッグ：基準線の点のうち矩形の中にある点の s の最小〜最大を、距離のグラフに帯で示す。
// - クリック・ダブルクリック、Esc とツールバーの選択のチップ（main.ts）で解除する。
// - 距離のグラフの上でカーソルを動かすと、その s に縦線を引き、コース図と G-G 図に各周の位置を点で示す。

import * as d3 from "d3";
import { SELECT_COLOR, type Chart } from "./charts";
import { sRangeInRect, type Lap, type Range, type Refline } from "./data";

/** どのグラフで選んだか。"restored" は描き直しの前の選択で、ドラッグの枠は残っていない。 */
export type DistanceId = "speed" | "dt" | "offset" | "ax" | "pedal";
/** "panel" は区間ごとの損失のパネル（sections.ts）で区間を押したとき */
export type Source = DistanceId | "map" | "panel" | "restored";

export interface Selection {
  source: Source;
  range: Range;
}

interface Listener {
  onSelect: (sel: Selection | null) => void;
  onHover: (s: number | null) => void;
}

/**
 * グラフどうしで選択とカーソルの位置を共有する。
 * `onChange` は選択が変わるたびに呼ぶ。描き直し（reset）でも外れないので、グラフの外（ツールバーの選択のチップ）が使う。
 */
export class Link {
  private sel: Selection | null = null;
  private listeners: Listener[] = [];

  constructor(private readonly onChange: (sel: Selection | null) => void = () => {}) {}

  get selection(): Selection | null {
    return this.sel;
  }

  /** 描き直しの前に呼ぶ。選んだ範囲は残し、どのグラフの枠かは忘れる。 */
  reset(): void {
    this.listeners = [];
    if (this.sel) this.sel = { source: "restored", range: this.sel.range };
  }

  add(l: Listener): void {
    this.listeners.push(l);
    l.onSelect(this.sel);
  }

  select(sel: Selection | null): void {
    this.sel = sel;
    for (const l of this.listeners) l.onSelect(sel);
    this.onChange(sel);
  }

  hover(s: number | null): void {
    for (const l of this.listeners) l.onHover(s);
  }
}

type Overlay = d3.Selection<SVGGElement, unknown, null, undefined>;

/** 選んだ区間の帯と、ドラッグの枠の塗りの濃さ。どちらも同じ見た目にする */
const SELECT_OPACITY = 0.15;

/**
 * d3-brush の枠（ドラッグしている間と、離した後に残る矩形）を選択の色で塗る。既定の灰色のままだと、
 * ドラッグしたグラフだけ選択の色が違って見える。d3-brush は枠を作るときにしか塗らないので、作った後に上書きする。
 */
function paintBrush(g: d3.Selection<SVGGElement, unknown, null, undefined>): void {
  g.select(".selection").attr("fill", SELECT_COLOR).attr("fill-opacity", SELECT_OPACITY).attr("stroke", SELECT_COLOR);
}

function overlay(chart: Chart): Overlay {
  return d3.select(chart.svg).append("g").attr("pointer-events", "none");
}

function frame(chart: Chart): [[number, number], [number, number]] {
  const [xa, xb] = chart.x.range;
  const [ya, yb] = chart.y.range;
  return [
    [Math.min(xa, xb), Math.min(ya, yb)],
    [Math.max(xa, xb), Math.max(ya, yb)],
  ];
}

/** 距離のグラフ（横軸が s）に、横の区間選択とカーソルを付ける。 */
export function attachDistance(chart: Chart, id: DistanceId, link: Link): void {
  const { x } = chart;
  const [[left, top], [right, bottom]] = frame(chart);
  const svg = d3.select(chart.svg);
  const layer = overlay(chart);
  const band = layer
    .append("rect")
    .attr("y", top)
    .attr("height", bottom - top)
    .attr("fill", SELECT_COLOR)
    .attr("fill-opacity", SELECT_OPACITY)
    .attr("display", "none");
  const rule = layer
    .append("line")
    .attr("y1", top)
    .attr("y2", bottom)
    .attr("stroke", "black")
    .attr("stroke-opacity", 0.5)
    .attr("display", "none");

  const brush = d3
    .brushX()
    .extent([
      [left, top],
      [right, bottom],
    ])
    .on("start brush end", (ev: d3.D3BrushEvent<unknown>) => {
      if (!ev.sourceEvent) return; // brush.move による変更
      const px = ev.selection as [number, number] | null;
      if (px) link.select({ source: id, range: [x.invert(px[0]), x.invert(px[1])] });
      else if (ev.type === "end") link.select(null);
    });
  const g = svg.append("g").call(brush);
  paintBrush(g);

  svg
    .on("pointermove", (ev: PointerEvent) => {
      const [px] = d3.pointer(ev, chart.svg);
      link.hover(px >= left && px <= right ? Math.round(x.invert(px)) : null);
    })
    .on("pointerleave", () => link.hover(null))
    .on("dblclick", () => link.select(null));

  link.add({
    onSelect: (sel) => {
      const own = sel?.source === id;
      if (!own && d3.brushSelection(g.node()!)) g.call(brush.move, null);
      if (sel && !own) {
        const [a, b] = sel.range.map(x.apply) as [number, number];
        band.attr("display", null).attr("x", Math.min(a, b)).attr("width", Math.abs(b - a));
      } else {
        band.attr("display", "none");
      }
    },
    onHover: (s) => {
      if (s == null) rule.attr("display", "none");
      else rule.attr("display", null).attr("x1", x.apply(s)).attr("x2", x.apply(s));
    },
  });
}

/** コース図に、矩形の区間選択・選んだ区間の強調・カーソル位置の点を付ける。 */
export function attachMap(
  chart: Chart,
  line: Refline,
  laps: Lap[],
  color: (lap: Lap) => string,
  link: Link,
): void {
  const { x, y } = chart;
  const svg = d3.select(chart.svg);
  const layer = overlay(chart);
  const path = layer
    .append("path")
    .attr("fill", "none")
    .attr("stroke", SELECT_COLOR)
    .attr("stroke-width", 4)
    .attr("stroke-linecap", "round");
  const dots = layer.append("g");
  const toPath = d3
    .line<number>()
    .x((i) => x.apply(line.x[i]!))
    .y((i) => y.apply(line.y[i]!));

  const rangeOf = (px: [[number, number], [number, number]]) =>
    sRangeInRect(line, x.invert(px[0][0]), x.invert(px[1][0]), y.invert(px[0][1]), y.invert(px[1][1]));

  const brush = d3
    .brush()
    .extent(frame(chart))
    .on("start brush end", (ev: d3.D3BrushEvent<unknown>) => {
      if (!ev.sourceEvent) return;
      const px = ev.selection as [[number, number], [number, number]] | null;
      const range = px && rangeOf(px);
      // 矩形が基準線にかかっていない間は前の選択を残し、離した時点でかかっていなければ解除する
      if (range) link.select({ source: "map", range });
      else if (ev.type === "end") link.select(null);
    });
  const g = svg.append("g").call(brush);
  paintBrush(g);
  svg.on("dblclick", () => link.select(null));

  link.add({
    onSelect: (sel) => {
      if (sel?.source !== "map" && d3.brushSelection(g.node()!)) g.call(brush.move, null);
      if (!sel) {
        path.attr("d", null);
        return;
      }
      const [a, b] = sel.range;
      const idx = d3.range(line.s.length).filter((i) => line.s[i]! >= a && line.s[i]! <= b);
      path.attr("d", toPath(idx));
    },
    onHover: (s) => {
      const pts = s == null ? [] : laps.filter((l) => l.x[s] != null && l.y[s] != null);
      dots
        .selectAll("circle")
        .data(pts)
        .join("circle")
        .attr("r", 4)
        .attr("cx", (l) => x.apply(l.x[s!]!))
        .attr("cy", (l) => y.apply(l.y[s!]!))
        .attr("fill", color)
        .attr("stroke", "white");
    },
  });
}

/**
 * グラフに、カーソルの s での各周の点を描く。`at` は周と s から、その点のデータ座標（なければ null）を返す。
 * G-G 図は描き直すたびに変わるので、グラフは呼ぶたびに受け取る。
 */
export function drawDots(
  chart: Chart,
  laps: Lap[],
  color: (lap: Lap) => string,
  s: number | null,
  at: (lap: Lap, s: number) => [number, number] | null,
): void {
  const svg = d3.select(chart.svg);
  let layer = svg.select<SVGGElement>("g.hover-dots");
  if (layer.empty()) layer = svg.append("g").attr("class", "hover-dots").attr("pointer-events", "none");
  const pts = s == null ? [] : laps.flatMap((l) => {
    const p = at(l, s);
    return p ? [{ l, p }] : [];
  });
  layer
    .selectAll("circle")
    .data(pts)
    .join("circle")
    .attr("r", 5)
    .attr("cx", (d) => chart.x.apply(d.p[0]))
    .attr("cy", (d) => chart.y.apply(d.p[1]))
    .attr("fill", (d) => color(d.l))
    .attr("stroke", "white")
    .attr("stroke-width", 1.5);
}

/** G-G 図の点の座標 (ay, ax)。 */
export const gAt = (l: Lap, s: number): [number, number] | null =>
  l.ax[s] != null && l.ay[s] != null ? [l.ay[s]!, l.ax[s]!] : null;

/** コース図の点の座標 (x, y)。 */
export const xyAt = (l: Lap, s: number): [number, number] | null =>
  l.x[s] != null && l.y[s] != null ? [l.x[s]!, l.y[s]!] : null;
