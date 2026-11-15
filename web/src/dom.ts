// 画面の部品で、複数のファイルが同じ形で使うもの：数値の書式、メッセージ、線の見本、周の名前、表の見出し。

import type { LapStyle } from "./charts";
import type { Lap } from "./data";

/** complete でない周（計測線の前後だけの切れ端など）の呼び名。一覧、Δt の欄、title で同じ語を使う */
export const FRAGMENT = "断片";

/** 重ねる周がないときに、グラフの代わりに出す文 */
export const PICK_LAPS = "重ねる周を一覧で選ぶ";

/** 数値の表示。値がなければ "N/A"。`sign` なら正の値に + を付ける */
export function fmt(v: number | null | undefined, digits: number, sign = false): string {
  if (v == null) return "N/A";
  const s = v.toFixed(digits);
  return sign && v > 0 ? `+${s}` : s;
}

/** 図や表の代わりに出す 1 行の文 */
export function message(target: HTMLElement, text: string, title?: string): void {
  const p = document.createElement("p");
  p.className = "message";
  p.textContent = text;
  if (title) p.title = title;
  target.replaceChildren(p);
}

/** 周の線の見本（色、太さ、破線） */
export function swatch(style: LapStyle): SVGSVGElement {
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

const SOURCE_SHORT: Record<string, string> = { racechrono: "RC", ac: "AC" };

/** 画面に出すソースの短い名前（RC、AC）。知らないソースはそのまま */
export function sourceName(source: string): string {
  return SOURCE_SHORT[source] ?? source;
}

/**
 * 画面に出す周の短い名前。例「RC 10/12 12:00 #10」。session_id（ingest が書く UTC の `%Y%m%dT%H%M%SZ`）を
 * 現地時刻の月/日 時:分にする。同じ日に同じソースのセッションが 2 つあっても衝突しないよう時刻を含める。
 * 周を指すキーは `lap.label` のままで、これは表示だけに使う（元の label は title に出す）。
 * session_id がこの形でなければ、そのまま出す。
 */
export function lapLabel(lap: Lap): string {
  const src = sourceName(lap.source);
  const m = /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(lap.session_id);
  if (!m) return `${src} ${lap.session_id} #${lap.lap}`;
  const [, y, mo, d, h, mi, s] = m.map(Number) as number[];
  const t = new Date(Date.UTC(y!, mo! - 1, d!, h!, mi!, s!));
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${src} ${t.getMonth() + 1}/${t.getDate()} ${pad(t.getHours())}:${pad(t.getMinutes())} #${lap.lap}`;
}

/** 周のタイム [s] の表示。complete でない周は FRAGMENT */
export function lapTime(lap: Lap): string {
  return lap.complete ? fmt(lap.t_end, 3) : FRAGMENT;
}

/** 線の見本と短い名前を並べた周の名前。元の label は title に出す */
export function lapName(lap: Lap, style: LapStyle): HTMLSpanElement {
  const span = document.createElement("span");
  span.className = "lap-label";
  span.title = lap.label;
  span.append(swatch(style), lapLabel(lap));
  return span;
}

export interface Head {
  text: string;
  title?: string;
}

/** 表の見出しの行を作り直す */
export function setHead(table: HTMLTableElement, cols: (string | Head)[]): void {
  const head = table.createTHead();
  head.replaceChildren();
  const tr = head.insertRow();
  for (const c of cols) {
    const th = document.createElement("th");
    const h = typeof c === "string" ? { text: c } : c;
    th.textContent = h.text;
    if (h.title) th.title = h.title;
    tr.append(th);
  }
}
