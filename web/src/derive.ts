// 画面の状態と、状態から求める値。DOM には触らない。
//
// - State は操作欄の値の唯一の正。操作欄（ツールバーのチップと周を選ぶ一覧、チェックボックス）は state から書き、state は操作のハンドラだけが変える。
// - normalize は、基準ラップと比較の周を候補の中に戻す。状態を変える側（ハンドラと openCourse）が 1 回だけ呼ぶ。
// - derive は状態から描くものを求める純粋関数。損失とコーナーの基準との差はここで 1 回だけ求め、
//   損失のパネル、コーナーのチップ、コーナーの表に同じ値を渡す。

import { lapColors, lapStyle, PAIR_COLORS, type LapStyle } from "./charts";
import {
  defaultComparison,
  defaultSelection,
  refCandidates,
  sectionTime,
  shownLaps,
  timeDelta,
  type CourseData,
  type Lap,
  type Series,
} from "./data";
import { hasOtherBest, sectionLosses, type SectionLoss } from "./sections";

export interface State {
  showInvalid: boolean;
  /** 重ねる周（label）。multi のときだけ使う */
  selected: Set<string>;
  /** false なら基準と比較の 2 周だけ描く（既定）。true なら一覧のチェックで選んだ周を重ねる */
  multi: boolean;
  /** 基準ラップ（label）。null は「（選ぶ）」 */
  ref: string | null;
  /** 2 周で比べるときの比較の周（label）。null は「（なし）」 */
  cmp: string | null;
  view: "overview" | "corner";
  /** 選んだコーナーの id。コーナーのビューで拡大するコーナー */
  corner: string | null;
}

function lapOf(doc: CourseData, label: string | null): Lap | null {
  return label == null ? null : (doc.laps.find((l) => l.label === label) ?? null);
}

/** 比較の周の候補：一覧に出す周のうち、基準以外で基準と同じ組の周（基準がなければ全部） */
export function cmpCandidates(doc: CourseData, showInvalid: boolean, ref: Lap | null): Lap[] {
  return shownLaps(doc.laps, showInvalid).filter((l) => l !== ref && (ref == null || l.group === ref.group));
}

/**
 * 基準ラップと比較の周を候補に戻す。選んでいた周が候補から外れたら既定に戻す（基準は default_ref、
 * 比較は defaultComparison）。「（選ぶ）」「（なし）」で外した（null の）ときは既定に戻さない。
 */
export function normalize(state: State, doc: CourseData): Pick<State, "ref" | "cmp"> {
  const cands = refCandidates(doc.laps, state.showInvalid);
  const has = (label: string | null) => cands.some((l) => l.label === label);
  const ref = state.ref == null || has(state.ref) ? state.ref : has(doc.default_ref) ? doc.default_ref : null;
  const refLap = lapOf(doc, ref);
  const cmps = cmpCandidates(doc, state.showInvalid, refLap);
  const cmp =
    state.cmp == null || cmps.some((l) => l.label === state.cmp)
      ? state.cmp
      : (defaultComparison(cmps, refLap)?.label ?? null);
  return { ref, cmp };
}

/** コースを開いたときの状態。表示の切り替え（showInvalid、multi）とビューは前の状態から引き継ぐ */
export function initialState(doc: CourseData, keep: Pick<State, "showInvalid" | "multi" | "view" | "corner">): State {
  const ref = doc.default_ref;
  const cmps = cmpCandidates(doc, keep.showInvalid, lapOf(doc, ref));
  const state: State = {
    ...keep,
    selected: defaultSelection(doc.laps, lapOf(doc, ref)?.group ?? null),
    ref,
    cmp: defaultComparison(cmps, lapOf(doc, ref))?.label ?? null,
  };
  return { ...state, ...normalize(state, doc) };
}

/** 区間ごとの損失（自己ベストとの差）。2 周で比べるときの比較の周について求める */
export interface Losses {
  lap: Lap;
  rows: SectionLoss[];
  /** 自己ベストを決める周が `lap` のほかにあるか。なければ損失は比べる意味がない */
  comparable: boolean;
}

/** コーナーの表の値。速度は km/h、s は m */
export interface CornerValues {
  t: number | null;
  s_brake: number | null;
  ax_min: number | null;
  v_entry: number | null;
  v_min: number | null;
  s_vmin: number | null;
  v_exit: number | null;
}

/** 基準との差を出す値（s_vmin は差にしない） */
export const DIFF_KEYS = ["t", "s_brake", "ax_min", "v_entry", "v_min", "v_exit"] as const;
export type DiffKey = (typeof DIFF_KEYS)[number];

export interface CornerRow {
  lap: Lap;
  values: CornerValues;
  /** 基準との差（この周 − 基準）。基準の行と、基準がないときは null。どちらかの値がなければその値は null */
  diff: Record<DiffKey, number | null> | null;
}

export interface CornerTable {
  /** 基準ラップの値。基準がなければ null */
  ref: CornerValues | null;
  rows: CornerRow[];
}

export interface Derived {
  refCandidates: Lap[];
  ref: Lap | null;
  /** default_ref が基準の候補にあるか（既定値があるか） */
  hasDefaultRef: boolean;
  cmpCandidates: Lap[];
  cmp: Lap | null;
  /** 一覧に出す周 */
  shown: Lap[];
  /** グラフに重ねる周 */
  overlaid: Lap[];
  style: (lap: Lap) => LapStyle;
  /** 一覧に出す周の、基準ラップとのタイム差。基準がなければ空 */
  dts: Map<string, Series>;
  losses: Losses | null;
  /** state.corner のコーナーの表。行は重ねる周（基準ラップを重ねていなければ先頭に足す）。コーナーがなければ null */
  corner: CornerTable | null;
}

export function cornerValues(doc: CourseData, lap: Lap, id: string): CornerValues {
  const st = lap.corner_stats?.[id];
  return {
    t: sectionTime(doc, lap, id),
    s_brake: st?.s_brake ?? null,
    ax_min: st?.ax_min ?? null,
    v_entry: st?.v_entry ?? null,
    v_min: st?.v_min ?? null,
    s_vmin: st?.s_vmin ?? null,
    v_exit: st?.v_exit ?? null,
  };
}

function cornerTable(doc: CourseData, laps: Lap[], ref: Lap | null, id: string): CornerTable {
  const refV = ref ? cornerValues(doc, ref, id) : null;
  return {
    ref: refV,
    rows: laps.map((lap) => {
      const values = cornerValues(doc, lap, id);
      const diff =
        refV == null || lap === ref
          ? null
          : (Object.fromEntries(
              DIFF_KEYS.map((k) => {
                const a = values[k];
                const b = refV[k];
                return [k, a == null || b == null ? null : a - b];
              }),
            ) as Record<DiffKey, number | null>);
      return { lap, values, diff };
    }),
  };
}

/**
 * 状態から描くものを求める。state は normalize 済みであること。基準や比較の周が候補にないなら、
 * 状態を変えた側が normalize を呼んでいないので失敗させる。
 */
export function derive(state: State, doc: CourseData): Derived {
  const refCands = refCandidates(doc.laps, state.showInvalid);
  const ref = lapOf(doc, state.ref);
  if (state.ref != null && (ref == null || !refCands.includes(ref))) {
    throw new Error(`基準ラップ ${state.ref} が候補にない（normalize を通していない）`);
  }
  const cmps = cmpCandidates(doc, state.showInvalid, ref);
  const cmp = lapOf(doc, state.cmp);
  if (state.cmp != null && (cmp == null || !cmps.includes(cmp))) {
    throw new Error(`比較の周 ${state.cmp} が候補にない（normalize を通していない）`);
  }
  const shown = shownLaps(doc.laps, state.showInvalid);
  const overlaid = state.multi
    ? shown.filter((l) => state.selected.has(l.label))
    : [ref, cmp].filter((l): l is Lap => l != null);
  const colors = state.multi
    ? lapColors(doc.laps)
    : new Map<string, string>([
        ...(ref ? [[ref.label, PAIR_COLORS.ref] as const] : []),
        ...(cmp ? [[cmp.label, PAIR_COLORS.cmp] as const] : []),
      ]);
  const style = (lap: Lap) => lapStyle(lap, colors, ref?.label ?? null);
  const dts = new Map<string, Series>(ref ? shown.map((l) => [l.label, timeDelta(l, ref)]) : []);
  // 損失は 2 周で比べるときの比較の周について出す。複数の周を重ねるときは出さない
  const rows = !state.multi && cmp ? sectionLosses(doc, cmp) : null;
  const losses = rows && cmp ? { lap: cmp, rows, comparable: hasOtherBest(doc, cmp) } : null;
  const hasCorner = doc.sections?.some((s) => s.kind === "corner" && s.id === state.corner) ?? false;
  return {
    refCandidates: refCands,
    ref,
    hasDefaultRef: refCands.some((l) => l.label === doc.default_ref),
    cmpCandidates: cmps,
    cmp,
    shown,
    overlaid,
    style,
    dts,
    losses,
    // 差の元になる値が表のどこかに出るよう、重ねていない基準ラップも表の先頭の行に入れる
    corner: hasCorner
      ? cornerTable(doc, ref && !overlaid.includes(ref) ? [ref, ...overlaid] : overlaid, ref, state.corner!)
      : null,
  };
}

/** コーナー `id` の損失（比較の周の、自己ベストとの差）。比べられないか、値がなければ null */
export function cornerLoss(losses: Losses | null, id: string): number | null {
  if (!losses?.comparable) return null;
  return losses.rows.find((r) => r.section.id === id)?.loss ?? null;
}
