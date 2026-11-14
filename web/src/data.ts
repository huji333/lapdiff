// 書き出したデータ（analysis.export）の読み込みと、画面の状態に依存する計算。
// 次の処理は Python の判定を写したもの（理由は analysis/export.py の docstring にある）。
// Python 側を変えたらここも合わせる。
// - timeDelta・finalDelta：analysis/data.py の time_delta・final_delta。書き出しの t は 1e-3 s に
//   丸めてあるので、Δt は time_delta と最大 1e-3 s ずれる。
// - shownLaps・refCandidates・defaultSelection：analysis/view1.py の周の絞り込み。

export type Series = (number | null)[];

export interface Lap {
  label: string;
  source: string;
  session_id: string;
  lap: number;
  lap_valid: boolean;
  complete: boolean;
  t_end: number;
  t: Series;
  speed_kmh: Series;
  x: Series;
  y: Series;
  /** 前後G [G]。加速が正 */
  ax: Series;
  /** 横G [G]。左旋回が正（ingest.derive） */
  ay: Series;
  /** アクセル・ブレーキのペダル（0〜1）。AC にはあり、CAN のない実車では全部 null */
  throttle: Series;
  brake: Series;
  /** 基準線からの横のずれ [m]。進行方向の左が正（analysis.export） */
  offset: Series;
  /** コーナーごとの値（corners と同じ順）。corners.toml がなければ null */
  corner_stats: CornerStat[] | null;
}

/** analysis.export の corner_stats。区間に欠けがある周は値が null。速度は km/h */
export interface CornerStat {
  id: string;
  t_section: number | null;
  v_min: number | null;
  s_vmin: number | null;
  v_entry: number | null;
  v_exit: number | null;
  /** ブレーキ開始点（ax が -brake_g を下回り始めた s）。ブレーキなし・欠けありなら null */
  s_brake: number | null;
  /** 前のコーナーの後からボトムまでの ax の最小値 [G]。欠けありなら null */
  ax_min: number | null;
}

export interface Corner {
  id: string;
  name: string;
  s_start: number;
  s_apex: number | null;
  s_end: number;
}

export interface Refline {
  s: number[];
  x: number[];
  y: number[];
}

export interface CourseData {
  course: string;
  length: number;
  grid_max: number;
  refline: Refline;
  corners: Corner[] | null;
  default_ref: string | null;
  /** ブレーキ開始点のしきい値 [G]（仮の値。analysis.export の BRAKE_G） */
  brake_g: number;
  laps: Lap[];
}

export type Range = [number, number];

async function fetchJson(url: string): Promise<unknown> {
  const res = await fetch(url);
  if (!res.ok) {
    throw new Error(`${url} を読めない（HTTP ${res.status}）。npm run data で書き出したか確かめる`);
  }
  return res.json();
}

export async function loadIndex(): Promise<string[]> {
  const doc = (await fetchJson("data/index.json")) as { courses: string[] };
  return doc.courses;
}

export async function loadCourse(course: string): Promise<CourseData> {
  const doc = (await fetchJson(`data/${course}.json`)) as CourseData;
  checkCourse(doc);
  return doc;
}

/** 配列の長さが書き出しの約束（周の配列は grid_max + 1）と合っているか。合わなければ失敗させる。 */
export function checkCourse(doc: CourseData): void {
  const n = doc.grid_max + 1;
  const r = doc.refline;
  if (r.x.length !== r.s.length || r.y.length !== r.s.length) {
    throw new Error(`${doc.course}: 基準線の s, x, y の長さが違う`);
  }
  for (const lap of doc.laps) {
    for (const key of ["t", "speed_kmh", "x", "y", "ax", "ay", "offset", "throttle", "brake"] as const) {
      if (lap[key].length !== n) {
        throw new Error(`${doc.course}: ${lap.label} の ${key} の長さが ${lap[key].length}（${n} のはず）`);
      }
    }
  }
}

/** 一覧に出す周。既定では lap_valid の周だけ。 */
export function shownLaps(laps: Lap[], showInvalid: boolean): Lap[] {
  return showInvalid ? laps : laps.filter((l) => l.lap_valid);
}

/** 基準ラップの候補：一覧に出す周のうち complete の周（marimo のビュー 1 と同じ）。 */
export function refCandidates(laps: Lap[], showInvalid: boolean): Lap[] {
  return shownLaps(laps, showInvalid).filter((l) => l.complete);
}

/**
 * 2 周で比べるときの既定の比較の周：`ref` 以外の有効で欠けのない周のうち、`ref` と別のソース
 * （実車と AC）の最速周。別のソースになければ同じソースの最速周。候補がなければ null。
 */
export function defaultComparison(laps: Lap[], ref: Lap | null): Lap | null {
  const cand = laps.filter((l) => l !== ref && l.lap_valid && l.complete).sort((a, b) => a.t_end - b.t_end);
  return cand.find((l) => ref == null || l.source !== ref.source) ?? cand[0] ?? null;
}

/** 既定で重ねる周：lap_valid の周。 */
export function defaultSelection(laps: Lap[]): Set<string> {
  return new Set(laps.filter((l) => l.lap_valid).map((l) => l.label));
}

/**
 * 基準ラップ `ref` とのタイム差 dt(s) = t_lap(s) - t_ref(s)（data.time_delta と同じ。丸めた t から引くので ±1e-3 s）。
 * complete でない周は t の原点が比べられないので全部 null。どちらかの周に s の行がなければ null。
 */
export function timeDelta(lap: Lap, ref: Lap): Series {
  if (!ref.complete) {
    throw new Error(`基準ラップ ${ref.label} は s が欠けている周（complete でない）`);
  }
  if (!lap.complete) return lap.t.map(() => null);
  return lap.t.map((t, i) => {
    const tr = ref.t[i];
    return t == null || tr == null ? null : t - tr;
  });
}

/** dt が出ている最後の s での dt（data.final_delta と同じ）。ひとつもなければ null。 */
export function finalDelta(dt: Series): { s: number; dt: number } | null {
  for (let i = dt.length - 1; i >= 0; i--) {
    const v = dt[i];
    if (v != null) return { s: i, dt: v };
  }
  return null;
}

/** G-G 図の点（横G `lat` = ay、前後G `lon` = ax）。s の範囲 `range` の中の点だけ。 */
export interface GPoint {
  s: number;
  lat: number;
  lon: number;
}

export function gPoints(lap: Lap, range: Range | null): (GPoint | null)[] {
  const [a, b] = range ? (range[0] <= range[1] ? range : [range[1], range[0]]) : [-Infinity, Infinity];
  return lap.ax.map((ax, s) => {
    const ay = lap.ay[s];
    // 範囲の外と null の点は null にして、線をそこで切る
    if (s < a || s > b || ax == null || ay == null) return null;
    return { s, lat: ay, lon: ax };
  });
}

/** ペダルのグラフの値：アクセルを上に、ブレーキを下（負）に [%]。範囲 `win` の外と、どちらも null の点は null */
export function pedalSeries(lap: Lap, win: Range): { throttle: Series; brake: Series } {
  const pct = (v: Series, sign: number): Series =>
    v.map((p, s) => (p == null || s < win[0] || s > win[1] ? null : sign * p * 100));
  return { throttle: pct(lap.throttle, 1), brake: pct(lap.brake, -1) };
}

/** ペダルの値が 1 つでもある周か。 */
export function hasPedals(lap: Lap): boolean {
  return lap.throttle.some((v) => v != null) || lap.brake.some((v) => v != null);
}

/** コーナーのビューで描く s の範囲：区間の前後に `pad` [m] 足し、0〜grid_max に収める。 */
export function cornerWindow(c: Corner, gridMax: number, pad: number): Range {
  return [Math.max(0, Math.floor(c.s_start - pad)), Math.min(gridMax, Math.ceil(c.s_end + pad))];
}

/**
 * x・y を同じ縮尺で、幅 `width`・高さ `height` の枠いっぱいに描くための範囲。
 * 狭い方の向きの範囲を広げて、枠の縦横比に合わせる。
 */
export function fitDomains(
  xExt: Range,
  yExt: Range,
  width: number,
  height: number,
  margin: number,
): { x: Range; y: Range } {
  const xSpan = xExt[1] - xExt[0];
  const ySpan = yExt[1] - yExt[0];
  if (!(xSpan > 0 && ySpan > 0)) throw new Error(`図の範囲が空（x ${xSpan} m、y ${ySpan} m）`);
  const mPerPx = Math.max(xSpan / (width - 2 * margin), ySpan / (height - 2 * margin));
  const half = (ext: Range, px: number): Range => {
    const mid = (ext[0] + ext[1]) / 2;
    const h = (mPerPx * (px - 2 * margin)) / 2;
    return [mid - h, mid + h];
  };
  return { x: half(xExt, width), y: half(yExt, height) };
}

/** 基準線の点のうち矩形の中にある点の s の最小〜最大。矩形がコースの離れた 2 か所にかかると間を全部含む。 */
export function sRangeInRect(line: Refline, x0: number, x1: number, y0: number, y1: number): Range | null {
  const [xa, xb] = x0 <= x1 ? [x0, x1] : [x1, x0];
  const [ya, yb] = y0 <= y1 ? [y0, y1] : [y1, y0];
  let lo = Infinity;
  let hi = -Infinity;
  line.s.forEach((s, i) => {
    const x = line.x[i]!;
    const y = line.y[i]!;
    if (x >= xa && x <= xb && y >= ya && y <= yb) {
      lo = Math.min(lo, s);
      hi = Math.max(hi, s);
    }
  });
  return lo <= hi ? [lo, hi] : null;
}

/** 基準線上の距離 s の位置（線形補間）。範囲外は端の点。 */
export function pointAt(line: Refline, s: number): { x: number; y: number } {
  const { s: ss, x, y } = line;
  if (ss.length === 0) throw new Error("基準線が空");
  if (s <= ss[0]!) return { x: x[0]!, y: y[0]! };
  const last = ss.length - 1;
  if (s >= ss[last]!) return { x: x[last]!, y: y[last]! };
  let lo = 0;
  let hi = last;
  while (hi - lo > 1) {
    const mid = (lo + hi) >> 1;
    if (ss[mid]! <= s) lo = mid;
    else hi = mid;
  }
  const f = (s - ss[lo]!) / (ss[hi]! - ss[lo]!);
  return { x: x[lo]! + f * (x[hi]! - x[lo]!), y: y[lo]! + f * (y[hi]! - y[lo]!) };
}
