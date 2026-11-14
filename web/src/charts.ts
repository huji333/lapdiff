// ビュー 1 のグラフ（Observable Plot）：コース図、距離×速度・タイム差・前後G・横G、G-G 図。
// グラフは操作のたびに描き直さない。区間の強調とカーソルは brush.ts が SVG に重ねて描く。

import * as Plot from "@observablehq/plot";
import * as d3 from "d3";
import {
  bearingAt,
  fitDomains,
  gPoints,
  pointAt,
  type Corner,
  type CourseData,
  type Edges,
  type Kerb,
  type Lap,
  type Range,
  type Series,
} from "./data";

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

/** コーナーの拡大図での周の線の太さ。基準ラップ（太い線）を 2.6、ほかを 2.2 にそろえ気味にする */
function cornerLineWidth(base: number): number {
  return base > 2 ? 2.6 : 2.2;
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

const ROAD_FILL = "#ececec";
/**
 * 全体のコース図で損失の色を塗る帯の太さ [px]。全体の図では路面（約 11 m）が 4〜5 px にしかならず、
 * 路面そのものを塗ると周の線と白い縁取りに隠れて色が見えない。そのため路面より太い帯を周の線の下に敷く
 */
const PAINT_BAND_WIDTH = 10;

/** ミニセクターごとの色（境界は s [m]）。null の区間は塗らない */
export interface Paint {
  bounds: number[];
  color: (string | null)[];
}
const EDGE_STROKE = "#a8a8a8";
/** 縁石の帯。赤い面に白い破線の縁を付け、紅白の縁石に見せる */
const KERB_FILL = "#d9534f";
const KERB_STROKE = "white";
const REFLINE_STROKE = "#c4c4c4";

const SVG_NS = "http://www.w3.org/2000/svg";

/**
 * 縁石の多角形のうち、基準線の格子の添字が [lo, hi] の点。進む側と戻る側を同じ範囲で切るので、残りも閉じた帯になる。
 * 範囲に 2 点未満しか残らなければ null
 */
export function kerbInRange(kerb: Kerb, lo: number, hi: number): [number, number][] | null {
  const pts = kerb.i.flatMap((i, k) => (i >= lo && i <= hi ? [[kerb.x[k]!, kerb.y[k]!] as [number, number]] : []));
  return pts.length >= 4 ? pts : null;
}

/**
 * コースの路面（左右の端の間を薄く塗った面）と端の細い線。周の線より先に（下に）置く。
 * `idx` は基準線の格子の添字。null なら 1 周全体を閉じた輪として描く（左右の輪を evenodd で塗り、間だけを面にする）。
 * `idx` があればその範囲の左の端を進み、右の端を戻る 1 つの多角形にする。
 */
function roadMarks(edges: Edges | null, idx: number[] | null): Plot.Markish[] {
  if (!edges) return [];
  const { left, right } = edges;
  const ii = idx ?? d3.range(left.x.length);
  const pick = (p: { x: number[]; y: number[] }) => ii.map((i) => [p.x[i]!, p.y[i]!] as const);
  const l = pick(left);
  const r = pick(right);
  const fill: Plot.RenderFunction = (_index, scales) => {
    const fx = scales.x!;
    const fy = scales.y!;
    const ring = (pts: (readonly [number, number])[]) =>
      "M" + pts.map(([x, y]) => `${fx(x)},${fy(y)}`).join("L") + "Z";
    const path = document.createElementNS(SVG_NS, "path");
    path.setAttribute("d", idx == null ? ring(l) + ring(r) : ring([...l, ...[...r].reverse()]));
    path.setAttribute("fill", ROAD_FILL);
    path.setAttribute("fill-rule", "evenodd");
    return path;
  };
  const edgeLine = (pts: (readonly [number, number])[]) => {
    const closed = idx == null ? [...pts, pts[0]!] : pts;
    return Plot.line(closed, { x: (d) => d[0], y: (d) => d[1], stroke: EDGE_STROKE, strokeWidth: 1 });
  };
  return [fill, ...kerbMarks(edges.kerbs, idx), edgeLine(pick(left)), edgeLine(pick(right))];
}

/**
 * 縁石の帯（拡大図だけ）。全体の図では縁石（幅 2 m 前後）が 1 px に満たず読めないので描かない
 */
function kerbMarks(kerbs: Kerb[], idx: number[] | null): Plot.Markish[] {
  if (idx == null || idx.length === 0) return [];
  const lo = idx[0]!;
  const hi = idx[idx.length - 1]!;
  const rings = kerbs.flatMap((k) => {
    const r = kerbInRange(k, lo, hi);
    return r ? [r] : [];
  });
  if (rings.length === 0) return [];
  const render: Plot.RenderFunction = (_index, scales) => {
    const fx = scales.x!;
    const fy = scales.y!;
    const g = document.createElementNS(SVG_NS, "g");
    for (const pts of rings) {
      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("d", "M" + pts.map(([x, y]) => `${fx(x)},${fy(y)}`).join("L") + "Z");
      path.setAttribute("fill", KERB_FILL);
      path.setAttribute("stroke", KERB_STROKE);
      path.setAttribute("stroke-width", "1");
      path.setAttribute("stroke-dasharray", "4,4");
      g.appendChild(path);
    }
    return g;
  };
  return [render];
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
  /** 路面をミニセクターごとに塗る色。null なら塗らない */
  paint: Paint | null = null,
): Chart {
  const { refline: line } = doc;
  // 損失の色は基準線に沿った太い帯で塗る。1 m 刻みでたどり、両端を共有させてつなぎ目に隙間を作らない
  const painted = paint
    ? paint.color.flatMap((color, i) => {
        if (color == null) return [];
        return d3.range(paint.bounds[i]!, paint.bounds[i + 1]! + 1).map((s) => ({ ...pointAt(line, s), i, color }));
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
        ...roadMarks(doc.edges, null),
        // 帯は路面と端の線の上、周の線の下に置く。帯が路面より太いので、端の線は塗った区間で隠れる
        Plot.line(painted, { x: "x", y: "y", z: "i", stroke: "color", strokeWidth: PAINT_BAND_WIDTH }),
        // 基準線は実車の周の平均で、コースの中心ではない。路面と紛れないよう細い線にする
        Plot.line(line.x, { x: Plot.identity, y: line.y, stroke: REFLINE_STROKE, strokeWidth: 1 }),
        // 損失の色で塗ったときは、周の線に白い縁取りを付けて赤い帯から浮かせる
        ...laps.flatMap((lap) => {
          const st = style(lap);
          const casing = paint
            ? [Plot.line(lap.x, { x: Plot.identity, y: lap.y, stroke: "white", strokeWidth: st.strokeWidth + 1 })]
            : [];
          return [...casing, Plot.line(lap.x, { x: Plot.identity, y: lap.y, ...st })];
        }),
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
 * 点の上に置くラベルの縦の位置（SVG の y、ラベルの中心）。重なるラベルは上へずらす。
 * 画面の下にある点から順に置き、すでに置いたラベルと幅 `w`・高さ `h` の枠が重なれば、その上に積む。
 * 点の位置 `pts` は px。返す配列は `pts` と同じ順。
 */
export function stackLabels(pts: { x: number; y: number }[], w: number, h: number, gap: number): number[] {
  const out: number[] = new Array<number>(pts.length);
  const placed: { x: number; y: number }[] = [];
  const order = d3.range(pts.length).sort((a, b) => pts[b]!.y - pts[a]!.y);
  for (const i of order) {
    const p = pts[i]!;
    let y = p.y - gap;
    // 積むと別のラベルに当たることがあるので、当たらなくなるまで繰り返す（置いたラベルの数で止まる）
    for (let k = 0; k <= placed.length; k++) {
      const hit = placed.filter((q) => Math.abs(q.x - p.x) < w && Math.abs(q.y - y) < h);
      if (hit.length === 0) break;
      y = Math.min(...hit.map((q) => q.y)) - h;
    }
    out[i] = y;
    placed.push({ x: p.x, y });
  }
  return out;
}

/** ボトム速度のラベルの枠 [px]：3 桁の太字 11 px に白い縁取りを足した幅と、行の高さ */
const VMIN_LABEL = { w: 26, h: 13, gap: 11 };

/**
 * ボトム速度の数字。点の真上に置き、重なる数字は上に積んで点と細い引き出し線で結ぶ。
 * Plot.text の dy は印ごとに 1 つなので、px を見て位置を決める描画関数にする。
 */
function vminLabels(bottoms: { x: number; y: number; v: number; color: string }[]): Plot.RenderFunction {
  return (_index, scales) => {
    const fx = scales.x!;
    const fy = scales.y!;
    const pts = bottoms.map((b) => ({ x: fx(b.x), y: fy(b.y) }));
    const ys = stackLabels(pts, VMIN_LABEL.w, VMIN_LABEL.h, VMIN_LABEL.gap);
    const g = document.createElementNS(SVG_NS, "g");
    // 引き出し線を先に描き、数字の白い縁取りで線の上を覆う
    bottoms.forEach((b, i) => {
      const p = pts[i]!;
      const y = ys[i]!;
      if (p.y - y <= VMIN_LABEL.gap + 0.5) return;
      const ln = document.createElementNS(SVG_NS, "line");
      for (const [k, v] of Object.entries({ x1: p.x, y1: p.y - 4, x2: p.x, y2: y + VMIN_LABEL.h / 2 - 2 })) {
        ln.setAttribute(k, String(v));
      }
      ln.setAttribute("stroke", b.color);
      ln.setAttribute("stroke-width", "1");
      g.append(ln);
    });
    bottoms.forEach((b, i) => {
      const t = document.createElementNS(SVG_NS, "text");
      t.setAttribute("x", String(pts[i]!.x));
      t.setAttribute("y", String(ys[i]!));
      t.setAttribute("dy", "0.32em");
      t.setAttribute("text-anchor", "middle");
      t.setAttribute("font-size", "11");
      t.setAttribute("font-weight", "bold");
      t.setAttribute("fill", b.color);
      t.setAttribute("stroke", "white");
      t.setAttribute("stroke-width", "3");
      t.setAttribute("paint-order", "stroke");
      t.textContent = b.v.toFixed(0);
      g.append(t);
    });
    return g;
  };
}

/**
 * コーナーの拡大図。s の範囲 `win` の路面（コースの端があれば）・基準線（細い破線）と、重ねる周の走行ラインを同じ縮尺で描く。
 * 区間の始点・終点に印を付け、各周のボトム速度（corner_stats の s_vmin）の位置に点と速度を出す。
 * ブレーキ開始点の三角は、その s での基準線の進む向きを指す。
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
  // コースの端も拡大図に収める
  if (doc.edges) {
    for (const side of [doc.edges.left, doc.edges.right]) {
      for (const i of idx) {
        xs.push(side.x[i]!);
        ys.push(side.y[i]!);
      }
    }
    for (const k of doc.edges.kerbs) {
      for (const [x, y] of kerbInRange(k, idx[0] ?? 0, idx[idx.length - 1] ?? -1) ?? []) {
        xs.push(x);
        ys.push(y);
      }
    }
  }
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
    return [{ lap: l, x: l.x[s]!, y: l.y[s]!, bearing: bearingAt(line, s) }];
  });
  const bottoms = laps.flatMap((l) => {
    const st = l.corner_stats?.[ci];
    const s = st?.s_vmin;
    if (s == null || l.x[s] == null || l.y[s] == null) return [];
    return [{ lap: l, x: l.x[s]!, y: l.y[s]!, v: st!.v_min!, color: style(l).stroke }];
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
        ...roadMarks(doc.edges, idx),
        // 基準線は実車の周の平均で、コースの中心ではない。路面と紛れないよう細い線にする
        Plot.line(rx, { x: Plot.identity, y: ry, stroke: REFLINE_STROKE, strokeWidth: 1, strokeDasharray: "4,3" }),
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
        // 周の線は白い縁取りの上に描く。重なっても、どちらが内側かを線の境目で読めるようにする。
        // 基準ラップとの太さの差は小さくし、太い線に細い線のずれが埋もれないようにする
        ...laps.flatMap((l) => {
          const st = style(l);
          const w = cornerLineWidth(st.strokeWidth);
          const common = { x: Plot.identity, y: cut(l.y), strokeLinejoin: "round", strokeLinecap: "round" } as const;
          return [
            Plot.line(cut(l.x), { ...common, stroke: "white", strokeWidth: w + 2.5 }),
            Plot.line(cut(l.x), { ...common, ...st, strokeWidth: w }),
          ];
        }),
        Plot.dot(brakes, {
          x: "x",
          y: "y",
          r: 6,
          symbol: "triangle",
          // 三角の頂点は既定で上（北）を指す。rotate は時計回りの角度なので、方位をそのまま渡す
          rotate: "bearing",
          fill: (d) => style(d.lap).stroke,
          stroke: "white",
        }),
        Plot.dot(bottoms, { x: "x", y: "y", r: 4, fill: (d) => style(d.lap).stroke, stroke: "white" }),
        vminLabels(bottoms),
      ],
    }),
  );
}
