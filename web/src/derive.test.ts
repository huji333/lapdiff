import { describe, expect, it } from "vitest";
import contract from "./fixtures/contract.json";
import type { CourseData } from "./data";
import { cornerLoss, derive, initialState, normalize, type State } from "./derive";
import { lapLabel } from "./dom";

// 契約の書き出し（fixtures/contract.json）の周：
//   ac ac1 #1（有効・完走 11.111 s）、racechrono rc1 #1（有効・完走 10.0 s、T1 でブレーキ）、
//   racechrono rc1 #2（有効・完走 9.101 s、default_ref）、racechrono rc1 #3（無効・断片）
const doc = (contract as unknown as { doc: CourseData }).doc;
const AC = "ac ac1 #1";
const RC1 = "racechrono rc1 #1";
const RC2 = "racechrono rc1 #2";
const RC3 = "racechrono rc1 #3";

const base = initialState(doc, { showInvalid: false, multi: false, view: "corner", corner: "T1" });
const labels = (laps: { label: string }[]) => laps.map((l) => l.label);

describe("initialState", () => {
  it("基準は default_ref、比較は別のソースの最速周、重ねる周は有効な周", () => {
    expect(base.ref).toBe(RC2);
    expect(base.cmp).toBe(AC);
    expect([...base.selected].sort()).toEqual([AC, RC1, RC2].sort());
  });
});

describe("normalize", () => {
  const norm = (patch: Partial<State>) => normalize({ ...base, ...patch }, doc);

  it("候補にない基準は default_ref に戻す", () => {
    expect(norm({ ref: RC3, showInvalid: true }).ref).toBe(RC2);
  });

  it("「（選ぶ）」「（なし）」で外した null は既定に戻さない", () => {
    expect(norm({ ref: null, cmp: null })).toEqual({ ref: null, cmp: null });
  });

  it("基準と同じ周になった比較は既定の比較に戻す", () => {
    expect(norm({ ref: AC, cmp: AC })).toEqual({ ref: AC, cmp: RC2 });
  });

  it("一覧から消えた比較は既定に戻す", () => {
    expect(norm({ cmp: RC3, showInvalid: false }).cmp).toBe(AC);
    expect(norm({ cmp: RC3, showInvalid: true }).cmp).toBe(RC3);
  });
});

describe("derive", () => {
  it("normalize を通していない状態は失敗させる", () => {
    expect(() => derive({ ...base, ref: RC3 }, doc)).toThrow(/normalize/);
    expect(() => derive({ ...base, cmp: RC2 }, doc)).toThrow(/normalize/);
  });

  it("2 周で比べるときは基準と比較を重ね、比較の周の損失を求める", () => {
    const d = derive({ ...base, cmp: RC1 }, doc);
    expect(labels(d.overlaid)).toEqual([RC2, RC1]);
    expect(labels(d.cmpCandidates)).toEqual([AC, RC1]);
    expect(d.losses?.lap.label).toBe(RC1);
    expect(d.losses?.comparable).toBe(true);
    // T1：rc1 #1 は 2.5 s、自己ベスト（rc1 #1 と #2 の最速）は 2.271 s
    expect(cornerLoss(d.losses, "T1")).toBeCloseTo(0.229, 9);
  });

  it("同じソースに自己ベストを決める周がほかになければ、損失は比べられない", () => {
    const d = derive(base, doc);
    expect(d.losses?.lap.label).toBe(AC);
    expect(d.losses?.comparable).toBe(false);
    expect(cornerLoss(d.losses, "T1")).toBeNull();
  });

  it("複数の周を重ねるときは選んだ周を重ね、損失は出さない", () => {
    const d = derive({ ...base, multi: true, selected: new Set([RC1, RC3]) }, doc);
    // RC3 は無効な周なので、一覧に出さないかぎり重ねない
    expect(labels(d.overlaid)).toEqual([RC1]);
    expect(d.losses).toBeNull();
  });

  it("基準がなければ Δt もコーナーの差も出さない", () => {
    const d = derive({ ...base, ref: null }, doc);
    expect(d.dts.size).toBe(0);
    expect(d.corner?.ref).toBeNull();
    expect(d.corner?.rows.every((r) => r.diff == null)).toBe(true);
  });

  it("コーナーの表：基準の行は差を持たず、ほかの行は基準との差", () => {
    const d = derive({ ...base, cmp: RC1 }, doc);
    const [refRow, row] = d.corner!.rows;
    expect(refRow!.lap.label).toBe(RC2);
    expect(refRow!.diff).toBeNull();
    expect(row!.values.s_brake).toBe(15);
    expect(row!.diff!.t).toBeCloseTo(2.5 - 2.271, 9);
    expect(row!.diff!.v_min).toBeCloseTo(90 - 144, 9);
    expect(row!.diff!.ax_min).toBeCloseTo(-0.5, 9);
    // 基準はブレーキ開始点がない（しきい値を下回らない）ので、差は出さない
    expect(d.corner!.ref!.s_brake).toBeNull();
    expect(row!.diff!.s_brake).toBeNull();
  });

  it("コースにないコーナーなら表は null", () => {
    expect(derive({ ...base, corner: "T9" }, doc).corner).toBeNull();
  });

  it("2 周で比べるときの色は基準が青、比較が橙", () => {
    const d = derive(base, doc);
    const [ref, cmp] = d.overlaid;
    expect(d.style(ref!).stroke).not.toBe(d.style(cmp!).stroke);
    expect(d.style(ref!).strokeWidth).toBeGreaterThan(d.style(cmp!).strokeWidth);
  });
});

describe("lapLabel", () => {
  it("ソースを略し、session_id の UTC を現地時刻の月/日 時:分にする", () => {
    const lap = { ...doc.laps[0]!, source: "racechrono", session_id: "20261012T030044Z", lap: 10 };
    const t = new Date(Date.UTC(2026, 9, 12, 3, 0, 44));
    const hm = `${String(t.getHours()).padStart(2, "0")}:${String(t.getMinutes()).padStart(2, "0")}`;
    expect(lapLabel(lap)).toBe(`RC ${t.getMonth() + 1}/${t.getDate()} ${hm} #10`);
  });

  it("session_id が時刻の形でなければそのまま出す", () => {
    expect(lapLabel(doc.laps[0]!)).toBe("AC ac1 #1");
  });
});
