// ビュー 1 のグラフ（Observable Plot）：コース図、距離×速度・タイム差・前後G・横G、G-G 図。
// グラフは操作のたびに描き直さない。区間の強調とカーソルは brush.ts が SVG に重ねて描く。

import * as Plot from "@observablehq/plot";
import * as d3 from "d3";
import { fitDomains, gPoints, pointAt, type Corner, type CourseData, type Lap, type Range, type Series } from "./data";

export const HIGHLIGHT = "#d62728";
const CORNER_GRAY = "#808080";
const S_TICK = 500;
const MAP_MARGIN = 12;
const PLOT_WIDTH = 760;
const PLOT_HEIGHT = 240;
const GG_SIZE = 340;
/** G-G 図の目安の円 [G] */
const G_RINGS = [0.5, 1.0, 1.5];

/** 描いたグラフと、その x・y の縮尺（データ ⇄ SVG 座標）。 */
export interface Chart {
  svg: SVGSVGElement;
  x: Scale;
  y: Scale;
}

export interface Scale {
  apply: (v: number) => number;
  invert: (px: number) => number;
  range: [number, number];
}

export interface LapStyle {
  stroke: string;
  strokeWidth: number;
  strokeDasharray: string | undefined;
}

/** 周の色。全周の並び（lap_list の順）で決め、重ねる周を変えても色が変わらないようにする。 */
/** 2 周で比べるときの色：基準は青、比較は橙。 */
export const PAIR_COLORS = { ref: "#4e79a7", cmp: "#f28e2b" } as const;

export function lapColors(laps: Lap[]): Map<string, string> {
  const scheme = d3.schemeTableau10;
  return new Map(laps.map((l, i) => [l.label, scheme[i % scheme.length]!]));
}

/** 基準ラップは太く、AC は破線にする。 */
export function lapStyle(lap: Lap, colors: Map<string, string>, ref: string | null): LapStyle {
  return {
    stroke: colors.get(lap.label) ?? "#aaa",
    strokeWidth: lap.label === ref ? 2.6 : 1.6,
    strokeDasharray: lap.source === "ac" ? "5,3" : undefined,
  };
}

function scaleOf(plot: Plot.Plot, name: "x" | "y"): Scale {
  const s = plot.scale(name);
  if (!s?.apply || !s.invert || !s.range) throw new Error(`グラフに連続の ${name} の縮尺がない`);
  const [r0, r1] = s.range as number[];
  return { apply: s.apply, invert: s.invert, range: [r0!, r1!] };
}

function asChart(plot: (SVGSVGElement | HTMLElement) & Plot.Plot): Chart {
  if (!(plot instanceof SVGSVGElement)) throw new Error("Plot が svg 以外を返した（凡例や題を付けていないか）");
  return { svg: plot, x: scaleOf(plot, "x"), y: scaleOf(plot, "y") };
}

/**
 * 高さを決めて x と y を同じ縮尺（1 m あたりの px）で描くときのグラフの幅。
 * Plot の aspectRatio は既定の余白から高さを決め、指定した margin を左右の幅から引かないため使わない。
 */
export function equalScaleWidth(height: number, margin: number, xSpan: number, ySpan: number): number {
  if (!(xSpan > 0 && ySpan > 0)) throw new Error(`コース図の範囲が空（x ${xSpan} m、y ${ySpan} m）`);
  return ((height - 2 * margin) * xSpan) / ySpan + 2 * margin;
}

/**
 * 基準線のコース図。x 東・y 北を同じ縮尺で描き、重ねる周の走行ラインを色で重ねる。
 * 高さ `height` を画面に合わせて決め、幅はコースの縦横比から求める。
 */
export function courseMap(
  doc: CourseData,
  laps: Lap[],
  style: (lap: Lap) => LapStyle,
  height: number,
  /** 基準線をミニセクターごとに塗る色。null なら灰色の 1 本 */
  paint: { bounds: number[]; color: (string | null)[] } | null = null,
): Chart {
  const { refline: line } = doc;
  // ミニセクターごとに基準線を 1 m 刻みでたどった点。両端を共有させ、つなぎ目に隙間を作らない
  const painted = paint
    ? paint.color.flatMap((color, i) => {
        if (color == null) return [];
        const pts = d3.range(paint.bounds[i]!, paint.bounds[i + 1]! + 1).map((s) => ({ ...pointAt(line, s), i, color }));
        return pts;
      })
    : [];
  const [x0, x1] = d3.extent(line.x) as [number, number];
  const [y0, y1] = d3.extent(line.y) as [number, number];
  const pad = 0.05 * Math.max(x1 - x0, y1 - y0);
  const width = equalScaleWidth(height, MAP_MARGIN, x1 - x0 + 2 * pad, y1 - y0 + 2 * pad);
  const ticks = d3.range(0, doc.length, S_TICK).map((s) => ({ s, ...pointAt(line, s) }));
  const apexes = (doc.corners ?? [])
    .map((c, ci) => ({ ...c, ci }))
    .filter((c) => c.s_apex != null)
    .map((c) => ({ ...c, ...pointAt(line, c.s_apex!) }));
  // 各コーナーの記号の下に、重ねる周の最低通過速度 [km/h] を周の色で縦に並べる
  const vmins = apexes.flatMap((c) =>
    laps.flatMap((lap) => {
      const v = lap.corner_stats?.[c.ci]?.v_min;
      return v == null ? [] : [{ x: c.x, y: c.y, v, lap }];
    }),
  );
  return asChart(
    Plot.plot({
      width,
      height,
      margin: MAP_MARGIN,
      x: { domain: [x0 - pad, x1 + pad], axis: null },
      y: { domain: [y0 - pad, y1 + pad], axis: null },
      marks: [
        Plot.line(line.x, { x: Plot.identity, y: line.y, stroke: "#d0d0d0", strokeWidth: 7 }),
        Plot.line(painted, { x: "x", y: "y", z: "i", stroke: "color", strokeWidth: 9 }),
        ...laps.map((lap) => Plot.line(lap.x, { x: Plot.identity, y: lap.y, ...style(lap) })),
        Plot.dot(ticks, { x: "x", y: "y", symbol: "plus", stroke: "black", r: 4 }),
        Plot.text(ticks, { x: "x", y: "y", text: (d) => `${d.s}`, dx: 6, dy: -8, textAnchor: "start" }),
        Plot.text(apexes, {
          x: "x",
          y: "y",
          text: "id",
          dx: 12,
          dy: 10,
          fontWeight: "bold",
          fontSize: 11,
          fill: CORNER_GRAY,
          stroke: "white",
          strokeWidth: 3,
          paintOrder: "stroke",
        }),
        ...laps.map((lap, i) =>
          Plot.text(
            vmins.filter((d) => d.lap === lap),
            {
              x: "x",
              y: "y",
              text: (d) => d.v.toFixed(0),
              dx: 12,
              dy: 22 + 11 * i,
              fontSize: 10,
              fontWeight: "bold",
              textAnchor: "start",
              fill: style(lap).stroke,
              stroke: "white",
              strokeWidth: 3,
              paintOrder: "stroke",
            },
          ),
        ),
      ],
    }),
  );
}

/** 距離のグラフ。`series` は周ごとの、s の格子の添字で並んだ値。 */
export function distanceChart(
  doc: CourseData,
  series: { lap: Lap; values: Series }[],
  style: (lap: Lap) => LapStyle,
  opts: {
    yLabel: string;
    zero: boolean;
    labelCorners: boolean;
    height?: number;
    width?: number;
    /** 描く s の範囲。既定は全長 */
    xDomain?: Range;
    /** 周の線の下に足す印（ブレーキ開始点の縦線など） */
    extra?: Plot.Markish[];
  },
): Chart {
  const corners = doc.corners ?? [];
  return asChart(
    Plot.plot({
      width: opts.width ?? PLOT_WIDTH,
      height: opts.height ?? PLOT_HEIGHT,
      marginLeft: 50,
      x: { domain: opts.xDomain ?? [0, doc.grid_max], label: "s [m]", ticks: opts.width ? d3.range(0, doc.grid_max + 1, 100) : undefined },
      y: { label: opts.yLabel, zero: opts.zero, grid: true },
      marks: [
        Plot.rect(corners, { x1: "s_start", x2: "s_end", fill: CORNER_GRAY, fillOpacity: 0.1, clip: true }),
        opts.labelCorners
          ? Plot.text(corners, {
              x: (c) => (c.s_start + c.s_end) / 2,
              text: "id",
              frameAnchor: "top",
              dy: 2,
              lineAnchor: "top",
              fill: CORNER_GRAY,
              fontSize: 10,
              clip: true,
            })
          : null,
        opts.zero ? Plot.ruleY([0], { stroke: "#999" }) : null,
        ...(opts.extra ?? []),
        ...series.map(({ lap, values }) =>
          Plot.line(values, { x: Plot.indexOf, y: Plot.identity, ...style(lap), clip: true }),
        ),
      ],
    }),
  );
}

/**
 * G-G 図の軸の範囲 ±d [G]。重ねる周の |ax|・|ay| の 99.5% 点を 0.25 G 単位で切り上げ、1 G を下限にする。
 * GPS の乱れで出る G のスパイク（SPEC「未決事項」）で軸が広がりすぎないよう、最大値は使わない。
 * 区間を選んでも範囲は変えない（選ぶたびに縮尺が変わると比べにくい）。
 */
export function gDomain(laps: Lap[]): number {
  const abs = laps.flatMap((l) => [...l.ax, ...l.ay]).filter((v): v is number => v != null).map(Math.abs);
  const q = d3.quantile(abs, 0.995) ?? 0;
  return Math.max(1, Math.ceil(q / 0.25) * 0.25);
}

/**
 * G-G 図。横軸は横G（ay）を左右反転して右旋回を右に、縦軸は前後G（ax）で加速を上にとる。
 * 周全体の軌跡を薄く描き、区間 `range` を選んでいればその区間の軌跡を濃く重ねる。
 */
export function ggChart(laps: Lap[], style: (lap: Lap) => LapStyle, range: Range | null, d: number): Chart {
  const circle = (r: number) =>
    d3.range(0, 2 * Math.PI + 0.01, Math.PI / 60).map((a) => ({ x: r * Math.cos(a), y: r * Math.sin(a), r }));
  const rings = G_RINGS.filter((r) => r <= d).flatMap(circle);
  const lines = (pts: Lap, sel: Range | null, opacity: number) => {
    const st = style(pts);
    return Plot.line(gPoints(pts, sel), {
      x: (p) => p?.lat ?? NaN,
      y: (p) => p?.lon ?? NaN,
      stroke: st.stroke,
      strokeWidth: st.strokeWidth,
      strokeOpacity: opacity,
      clip: true,
    });
  };
  return asChart(
    Plot.plot({
      width: GG_SIZE,
      height: GG_SIZE,
      marginLeft: 40,
      marginRight: 16,
      marginTop: 16,
      marginBottom: 36,
      x: { domain: [-d, d], reverse: true, label: "← 左旋回　横G ay [G]　右旋回 →", labelAnchor: "center", grid: true },
      y: { domain: [-d, d], label: "前後G ax [G]", grid: true },
      marks: [
        Plot.line(rings, { x: "x", y: "y", z: "r", stroke: "#999", strokeDasharray: "2,3" }),
        Plot.text(G_RINGS.filter((r) => r <= d), { x: 0, y: (r) => r, text: (r) => `${r} G`, dy: -6, fill: "#999", fontSize: 10 }),
        Plot.ruleX([0], { stroke: "#bbb" }),
        Plot.ruleY([0], { stroke: "#bbb" }),
        ...laps.map((lap) => lines(lap, null, range ? 0.12 : 0.45)),
        ...(range ? laps.map((lap) => lines(lap, range, 1)) : []),
      ],
    }),
  );
}

/**
 * コーナーの拡大図。s の範囲 `win` の基準線（太い灰色）と、重ねる周の走行ラインを同じ縮尺で描く。
 * 区間の始点・終点に印を付け、各周のボトム速度（corner_stats の s_vmin）の位置に点と速度を出す。
 */
export function cornerMap(
  doc: CourseData,
  laps: Lap[],
  style: (lap: Lap) => LapStyle,
  corner: Corner,
  win: Range,
  width: number,
  height: number,
): Chart {
  const { refline: line } = doc;
  const idx = line.s.flatMap((s, i) => (s >= win[0] && s <= win[1] ? [i] : []));
  const rx = idx.map((i) => line.x[i]!);
  const ry = idx.map((i) => line.y[i]!);
  const xs = [...rx];
  const ys = [...ry];
  const cut = (v: Series) => v.map((p, s) => (s >= win[0] && s <= win[1] ? p : null));
  for (const l of laps) {
    for (let s = win[0]; s <= win[1]; s++) {
      if (l.x[s] != null && l.y[s] != null) {
        xs.push(l.x[s]!);
        ys.push(l.y[s]!);
      }
    }
  }
  const margin = 16;
  const dom = fitDomains(d3.extent(xs) as Range, d3.extent(ys) as Range, width, height, margin);
  const ends = [
    { label: "始点", ...pointAt(line, corner.s_start) },
    { label: "終点", ...pointAt(line, corner.s_end) },
  ];
  const ci = (doc.corners ?? []).findIndex((c) => c.id === corner.id);
  const brakes = laps.flatMap((l) => {
    const s = l.corner_stats?.[ci]?.s_brake;
    if (s == null || l.x[s] == null || l.y[s] == null) return [];
    return [{ lap: l, x: l.x[s]!, y: l.y[s]! }];
  });
  const bottoms = laps.flatMap((l) => {
    const st = l.corner_stats?.[ci];
    const s = st?.s_vmin;
    if (s == null || l.x[s] == null || l.y[s] == null) return [];
    return [{ lap: l, x: l.x[s]!, y: l.y[s]!, v: st!.v_min! }];
  });
  // 走る向き：区間の始点の少し手前から始点への矢印
  const a = pointAt(line, Math.max(win[0], corner.s_start - 15));
  const b = pointAt(line, corner.s_start);
  return asChart(
    Plot.plot({
      width,
      height,
      margin,
      x: { domain: dom.x, axis: null },
      y: { domain: dom.y, axis: null },
      marks: [
        Plot.line(rx, { x: Plot.identity, y: ry, stroke: "#e6e6e6", strokeWidth: 10, strokeLinecap: "round" }),
        Plot.arrow([{ a, b }], {
          x1: (d) => d.a.x,
          y1: (d) => d.a.y,
          x2: (d) => d.b.x,
          y2: (d) => d.b.y,
          stroke: "#999",
          strokeWidth: 2,
        }),
        Plot.dot(ends, { x: "x", y: "y", r: 5, fill: CORNER_GRAY }),
        Plot.text(ends, { x: "x", y: "y", text: "label", dx: 10, textAnchor: "start", fill: CORNER_GRAY, fontSize: 11 }),
        ...laps.map((l) =>
          Plot.line(cut(l.x), {
            x: Plot.identity,
            y: cut(l.y),
            ...style(l),
            strokeWidth: style(l).strokeWidth * 1.2,
            strokeOpacity: 0.9,
            strokeLinejoin: "round",
            strokeLinecap: "round",
          }),
        ),
        Plot.dot(brakes, {
          x: "x",
          y: "y",
          r: 6,
          symbol: "triangle",
          rotate: 180,
          fill: (d) => style(d.lap).stroke,
          stroke: "white",
        }),
        Plot.dot(bottoms, { x: "x", y: "y", r: 4, fill: (d) => style(d.lap).stroke, stroke: "white" }),
        Plot.text(bottoms, {
          x: "x",
          y: "y",
          text: (d) => `${d.v.toFixed(0)}`,
          dy: -10,
          fill: (d) => style(d.lap).stroke,
          fontSize: 11,
          fontWeight: "bold",
          stroke: "white",
          strokeWidth: 3,
          paintOrder: "stroke",
        }),
      ],
    }),
  );
}
