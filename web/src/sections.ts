// 区間ごとの損失：比較の周が、区間ごとの自己ベスト（同じソースの lap_valid の周、全期間）より何秒遅いか。
// 全体のビューのパネルに横棒で並べ、コース図は 50 m のミニセクターごとの差で塗る。
// 区間の定義と区間タイムは analysis.export が書く。自己ベストはここで section_times の最小から求める。

import * as d3 from "d3";
import type { Link } from "./brush";
import type { Losses } from "./derive";
import { fmt, lapLabel, message } from "./dom";
import {
  bestPool,
  lossVsBest,
  miniBounds,
  minPerIndex,
  sectorTimes,
  type CourseData,
  type Lap,
  type Section,
} from "./data";

/** コース図を塗るミニセクターの長さ [m]。実車の GPS の誤差で色が決まらないよう、25 m より長くとる */
export const MINI_STEP = 50;

export interface SectionLoss {
  section: Section;
  time: number | null;
  best: number | null;
  loss: number | null;
}

export interface MiniLoss {
  bounds: number[];
  loss: (number | null)[];
}

/**
 * 自己ベストを決める周が、`lap` のほかにあるか。なければ損失はすべて 0（`lap` が同じソースで唯一の有効な周）か、
 * 値がない（有効な周がない）ので、比べる意味がない。
 */
export function hasOtherBest(doc: CourseData, lap: Lap): boolean {
  return bestPool(doc.laps, lap).some((l) => l !== lap && l.section_times != null);
}

export function sectionLosses(doc: CourseData, lap: Lap): SectionLoss[] | null {
  if (!doc.sections || !lap.section_times) return null;
  const pool = bestPool(doc.laps, lap).flatMap((l) => (l.section_times ? [l.section_times] : []));
  const best = minPerIndex(pool, doc.sections.length);
  const loss = lossVsBest(lap.section_times, best);
  return doc.sections.map((section, i) => ({
    section,
    time: lap.section_times![i] ?? null,
    best: best[i] ?? null,
    loss: loss[i] ?? null,
  }));
}

export function miniLosses(doc: CourseData, lap: Lap): MiniLoss {
  const bounds = miniBounds(doc.grid_max, MINI_STEP);
  const pool = bestPool(doc.laps, lap).map((l) => sectorTimes(l, bounds));
  const best = minPerIndex(pool, bounds.length - 1);
  return { bounds, loss: lossVsBest(sectorTimes(lap, bounds), best) };
}

/** 損失の色の両端：差 0 の薄い灰色と、差の大きい濃い赤 */
export const LOSS_RAMP = ["#e4e4e4", "#b2182b"] as const;

/** ミニセクターの色。差 0 は薄い灰色、`max` [s] 以上は濃い赤。値がなければ null（塗らない） */
export function lossColor(max: number): (loss: number | null) => string | null {
  const scale = d3.scaleSequential(d3.interpolateRgb(...LOSS_RAMP)).domain([0, max]).clamp(true);
  return (loss) => (loss == null ? null : scale(loss));
}

/** コース図の色の凡例：色のランプと、何の差か */
export function lossLegend(max: number): HTMLElement {
  const box = document.createElement("div");
  box.className = "loss-legend";
  const ramp = document.createElement("span");
  ramp.className = "ramp";
  ramp.style.background = `linear-gradient(to right, ${LOSS_RAMP.join(", ")})`;
  box.append(`自己ベスト比　${MINI_STEP} m ごと　0`, ramp, `${fmt(max, 2)} s`);
  return box;
}

const STORE_KEY = "lapdiff.sectionPanel";

function loadCollapsed(): boolean {
  try {
    const v = JSON.parse(window.localStorage.getItem(STORE_KEY) ?? "{}") as { collapsed?: boolean };
    return v.collapsed ?? false;
  } catch {
    return false;
  }
}

function saveCollapsed(collapsed: boolean): void {
  try {
    window.localStorage.setItem(STORE_KEY, JSON.stringify({ collapsed }));
  } catch {
    // 保存できなくても描画には影響しない（プライベートウィンドウなど）
  }
}

/** パネルのたたみの状態を復元し、ボタンを付ける。起動時に 1 回だけ呼ぶ。 */
export function setupPanel(panel: HTMLElement, toggle: HTMLButtonElement): void {
  const setCollapsed = (c: boolean) => {
    panel.classList.toggle("collapsed", c);
    toggle.title = c ? "開く" : "たたむ";
    toggle.setAttribute("aria-label", toggle.title);
    toggle.setAttribute("aria-expanded", String(!c));
  };
  setCollapsed(loadCollapsed());
  toggle.addEventListener("click", () => {
    const c = !panel.classList.contains("collapsed");
    setCollapsed(c);
    saveCollapsed(c);
  });
}

/**
 * パネルの中身：合計（比較の周 − 区間ごとの自己ベストの合計）と、損失の大きい順の横棒。
 * `min_diff_s` 未満の差は灰色にする。行を押すとその区間を選び（ほかのグラフで強調）、コーナーは拡大へのリンクを付ける。
 */
export function renderPanel(body: HTMLElement, title: HTMLElement, doc: CourseData, losses: Losses, link: Link): void {
  const { lap, rows: all } = losses;
  title.textContent = "自己ベストとの差 [s]";
  title.title = `${lap.source} の有効な周（全セッション）の区間ごとの最速をつないだ値。ベストラップではない`;
  if (!losses.comparable) {
    message(body, `比べる ${lap.source} の有効な周がほかにない`);
    return;
  }
  const known = all.filter((r) => r.loss != null);
  const total = d3.sum(known, (r) => r.loss!);
  const missing = all.length - known.length;

  const summary = document.createElement("p");
  summary.className = "sp-summary";
  summary.textContent =
    `${lapLabel(lap)}：合計 ${fmt(total, 2, true)} s` + (missing ? `（欠けのある ${missing} 区間を除く）` : "");
  summary.title = lap.label;

  const max = Math.max(doc.min_diff_s * 2, d3.max(known, (r) => r.loss!) ?? 0);
  const rows = [...all].sort((a, b) => (b.loss ?? -Infinity) - (a.loss ?? -Infinity));
  const list = document.createElement("ol");
  list.className = "sp-list";
  for (const r of rows) {
    const li = document.createElement("li");
    const small = r.loss == null || r.loss < doc.min_diff_s;
    li.className = `sp-row ${r.section.kind}${small ? " small" : ""}`;
    li.title = `${r.section.name}（s = ${r.section.s_start}〜${r.section.s_end} m）　この周 ${fmt(r.time, 3)} s、自己ベスト ${fmt(r.best, 3)} s`;

    const name = document.createElement("span");
    name.className = "sp-name";
    name.textContent = r.section.id;
    const bar = document.createElement("span");
    bar.className = "sp-bar";
    const fill = document.createElement("span");
    fill.style.width = r.loss == null ? "0" : `${Math.min(100, (100 * r.loss) / max)}%`;
    bar.append(fill);
    const val = document.createElement("span");
    val.className = "sp-val";
    val.textContent = fmt(r.loss, 2, true);
    li.append(name, bar, val);
    if (r.section.kind === "corner") {
      const a = document.createElement("a");
      a.href = `#corner/${r.section.id}`;
      a.textContent = "拡大";
      a.addEventListener("click", (e) => e.stopPropagation());
      li.append(a);
    }
    li.addEventListener("click", () => link.select({ source: "panel", range: [r.section.s_start, r.section.s_end] }));
    list.append(li);
  }
  body.replaceChildren(summary, list);
}
