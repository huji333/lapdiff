import { describe, expect, it } from "vitest";
import contract from "./fixtures/contract.json";
import {
  bearingAt,
  bestPool,
  CHANNELS,
  SCHEMA,
  cornersOf,
  sectionTime,
  lossVsBest,
  miniBounds,
  minPerIndex,
  sectorTimes,
  checkCourse,
  cornerWindow,
  defaultComparison,
  fitDomains,
  defaultSelection,
  finalDelta,
  gPoints,
  hasPedals,
  pedalSeries,
  pointAt,
  refCandidates,
  sRangeInRect,
  shownLaps,
  timeDelta,
  type CourseData,
  type Lap,
  type Refline,
} from "./data";

function lap(label: string, t: (number | null)[], opts: Partial<Lap> = {}): Lap {
  const blank = t.map(() => 0);
  return {
    label,
    source: "racechrono",
    group: "real",
    session_id: "s",
    lap: 1,
    lap_valid: true,
    complete: true,
    t_end: 0,
    t,
    speed_kmh: blank,
    x: blank,
    y: blank,
    ax: blank,
    ay: blank,
    offset: blank,
    throttle: t.map(() => null),
    brake: t.map(() => null),
    section_times: null,
    corner_stats: null,
    ...opts,
  };
}

describe("timeDelta", () => {
  const ref = lap("ref", [0, 1, 2, 3]);

  it("同じ s どうしで t を引く", () => {
    const l = lap("a", [0, 1.5, 2.5, 4]);
    expect(timeDelta(l, ref)).toEqual([0, 0.5, 0.5, 1]);
  });

  it("どちらかに s の行がなければ null", () => {
    const l = lap("a", [0, null, 2.5, 4]);
    const r = lap("r", [0, 1, null, 3]);
    expect(timeDelta(l, r)).toEqual([0, null, null, 1]);
  });

  it("complete でない周は全部 null", () => {
    const l = lap("a", [null, 1, 2, null], { complete: false });
    expect(timeDelta(l, ref)).toEqual([null, null, null, null]);
  });

  it("基準ラップが complete でなければ失敗させる", () => {
    const r = lap("r", [0, 1, 2, 3], { complete: false });
    expect(() => timeDelta(ref, r)).toThrow(/complete でない/);
  });
});

describe("finalDelta", () => {
  it("dt が出ている最後の s", () => {
    expect(finalDelta([0, 0.5, 0.7, null])).toEqual({ s: 2, dt: 0.7 });
  });
  it("ひとつもなければ null", () => {
    expect(finalDelta([null, null])).toBeNull();
  });
});

describe("周の絞り込み", () => {
  const laps = [
    lap("valid", [0], {}),
    lap("invalid_complete", [0], { lap_valid: false }),
    lap("fragment", [0], { lap_valid: false, complete: false }),
  ];

  it("既定では lap_valid の周だけ出す", () => {
    expect(shownLaps(laps, false).map((l) => l.label)).toEqual(["valid"]);
    expect(shownLaps(laps, true)).toHaveLength(3);
  });

  it("基準ラップの候補は出している周のうち complete の周", () => {
    expect(refCandidates(laps, false).map((l) => l.label)).toEqual(["valid"]);
    expect(refCandidates(laps, true).map((l) => l.label)).toEqual(["valid", "invalid_complete"]);
  });

  it("既定で重ねるのは lap_valid の周", () => {
    expect([...defaultSelection(laps)]).toEqual(["valid"]);
  });
});

describe("基準線", () => {
  // 正方形を一周する基準線（s = 0..4、(0,0) → (1,0) → (1,1) → (0,1) → (0,0)）
  const line: Refline = { s: [0, 1, 2, 3, 4], x: [0, 1, 1, 0, 0], y: [0, 0, 1, 1, 0] };

  it("矩形の中の点の s の最小〜最大", () => {
    expect(sRangeInRect(line, 0.5, 1.5, -0.5, 1.5)).toEqual([1, 2]);
    // 端点を逆に渡しても同じ
    expect(sRangeInRect(line, 1.5, 0.5, 1.5, -0.5)).toEqual([1, 2]);
  });

  it("離れた 2 か所にかかると間を全部含む", () => {
    expect(sRangeInRect(line, -0.5, 0.5, -0.5, 0.5)).toEqual([0, 4]);
  });

  it("点がなければ null", () => {
    expect(sRangeInRect(line, 5, 6, 5, 6)).toBeNull();
  });

  it("s の位置を線形補間する", () => {
    expect(pointAt(line, 1.5)).toEqual({ x: 1, y: 0.5 });
    expect(pointAt(line, -1)).toEqual({ x: 0, y: 0 });
    expect(pointAt(line, 9)).toEqual({ x: 0, y: 0 });
  });
});

describe("checkCourse", () => {
  const doc = (laps: Lap[], edges: CourseData["edges"] = null): CourseData => ({
    schema: SCHEMA,
    channels: [...CHANNELS],
    course: "c",
    length: 3.5,
    grid_max: 3,
    refline: { s: [0, 1], x: [0, 1], y: [0, 1] },
    edges,
    sections: null,
    min_diff_s: 0.05,
    default_ref: null,
    brake_g: 0.3,
    laps,
  });

  it("周の配列が grid_max + 1 でなければ失敗させる", () => {
    expect(() => checkCourse(doc([lap("a", [0, 1, 2, 3])]))).not.toThrow();
    expect(() => checkCourse(doc([lap("a", [0, 1, 2])]))).toThrow(/a の t の長さが 3/);
  });

  it("版が違えば書き出し直すよう失敗させる", () => {
    const { schema: _, ...old } = doc([]);
    expect(() => checkCourse(old as CourseData)).toThrow(/版が なし.*npm run data で書き出し直す/);
    expect(() => checkCourse({ ...doc([]), schema: SCHEMA + 1 })).toThrow(/npm run data/);
  });

  it("チャンネルが違えば失敗させる", () => {
    expect(() => checkCourse({ ...doc([]), channels: ["t", "speed_kmh"] })).toThrow(/チャンネル/);
    const { brake: _, ...noBrake } = lap("a", [0, 1, 2, 3]);
    expect(() => checkCourse(doc([noBrake as Lap]))).toThrow(/a の brake の長さが undefined/);
  });

  it("コースの端の長さが基準線と違えば失敗させる", () => {
    const e = (n: number) => ({ x: Array(n).fill(0), y: Array(n).fill(0) });
    const edges = (n: number) => ({ left: e(2), right: e(n), kerbs: [], attribution: "", attribution_url: "" });
    expect(() => checkCourse(doc([], edges(2)))).not.toThrow();
    expect(() => checkCourse(doc([], edges(3)))).toThrow(/コースの端（right）/);
  });

  it("縁石の長さが違えば失敗させる", () => {
    const e = { x: [0, 0], y: [0, 0] };
    const bad = { left: e, right: e, attribution: "", attribution_url: "", kerbs: [{ side: "left" as const, i: [0, 1], x: [0], y: [0, 0] }] };
    expect(() => checkCourse(doc([], bad))).toThrow(/縁石（left）/);
  });

  it("区間ごと・コーナーごとの値が区間とコーナーに揃っていなければ失敗させる", () => {
    const sections: CourseData["sections"] = [
      { id: "T1", kind: "corner", name: "", s_start: 0, s_end: 2, s_apex: null, side: null },
      { id: "T1→", kind: "straight", name: "", s_start: 2, s_end: 3 },
    ];
    const stat = { v_min: null, s_vmin: null, v_entry: null, v_exit: null, s_brake: null, ax_min: null };
    const l = (opts: Partial<Lap>) => lap("a", [0, 1, 2, 3], { section_times: [1, 1], corner_stats: { T1: stat }, ...opts });
    expect(() => checkCourse({ ...doc([l({})]), sections })).not.toThrow();
    expect(() => checkCourse({ ...doc([l({ section_times: [1] })]), sections })).toThrow(/section_times の数/);
    expect(() => checkCourse({ ...doc([l({ corner_stats: { T2: stat } })]), sections })).toThrow(/corner_stats/);
    expect(() => checkCourse({ ...doc([l({ corner_stats: null })]), sections })).toThrow(/corner_stats/);
  });
});

describe("gPoints", () => {
  const l = lap("a", [0, 1, 2, 3], { ax: [0.1, null, -0.5, 0.2], ay: [0.3, 0.4, -0.6, null] });

  it("横G は ay、前後G は ax。null の点は null", () => {
    expect(gPoints(l, null)).toEqual([{ s: 0, lat: 0.3, lon: 0.1 }, null, { s: 2, lat: -0.6, lon: -0.5 }, null]);
  });
  it("s の範囲の外は null。範囲の向きは問わない", () => {
    expect(gPoints(l, [2, 0.5])).toEqual([null, null, { s: 2, lat: -0.6, lon: -0.5 }, null]);
  });
});

describe("cornerWindow", () => {
  const c = { id: "T1", kind: "corner" as const, name: "", s_start: 10.5, s_apex: null, s_end: 90.2, side: null };
  it("前後に pad を足して格子の整数にする", () => {
    expect(cornerWindow(c, 1000, 50)).toEqual([0, 141]);
    expect(cornerWindow(c, 100, 5)).toEqual([5, 96]);
    expect(cornerWindow(c, 100, 15)).toEqual([0, 100]);
  });
});

describe("fitDomains", () => {
  it("x と y の 1 m あたりの px が等しく、元の範囲を含む", () => {
    const { x, y } = fitDomains([0, 100], [0, 300], 500, 400, 10);
    expect((500 - 20) / (x[1] - x[0])).toBeCloseTo((400 - 20) / (y[1] - y[0]), 9);
    expect(x[0]).toBeLessThanOrEqual(0);
    expect(x[1]).toBeGreaterThanOrEqual(100);
    expect(y).toEqual([0, 300]);
  });
});

describe("pedalSeries", () => {
  it("アクセルは上、ブレーキは下の %。範囲の外は null", () => {
    const l = lap("a", [0, 1, 2, 3], { throttle: [1, 0.5, 0, null], brake: [0, 0, 0.8, null] });
    expect(pedalSeries(l, [1, 3])).toEqual({ throttle: [null, 50, 0, null], brake: [null, -0, -80, null] });
    expect(hasPedals(l)).toBe(true);
    expect(hasPedals(lap("b", [0, 1]))).toBe(false);
  });
});

describe("defaultComparison", () => {
  const ref = lap("rc best", [0], { t_end: 130 });
  const rc2 = lap("rc 2", [0], { t_end: 131 });
  const ac = lap("ac 2", [0], { source: "ac", t_end: 128 });
  const acSlow = lap("ac 3", [0], { source: "ac", t_end: 129, complete: false });
  it("基準と別のソースの、有効で欠けのない最速周", () => {
    expect(defaultComparison([ref, rc2, ac, acSlow], ref)).toBe(ac);
  });
  it("別のソースになければ同じソースの最速周。なければ null", () => {
    expect(defaultComparison([ref, rc2], ref)).toBe(rc2);
    expect(defaultComparison([ref], ref)).toBeNull();
  });
});

describe("区間の自己ベスト", () => {
  it("同じソースの lap_valid の周だけを母集団にする", () => {
    const a = lap("a", [0]);
    const b = lap("b", [0], { lap_valid: false });
    const c = lap("c", [0], { source: "ac" });
    const d = lap("d", [0]);
    expect(bestPool([a, b, c, d], a).map((l) => l.label)).toEqual(["a", "d"]);
  });

  it("区間ごとの最小と、それとの差を求める。null は飛ばす", () => {
    const best = minPerIndex([[3, null, 5], [2, null, 6]], 3);
    expect(best).toEqual([2, null, 5]);
    expect(lossVsBest([3, 1, null], best)).toEqual([1, null, null]);
  });

  it("ミニセクターの境界は最後の短い区間を前に含める", () => {
    expect(miniBounds(120, 50)).toEqual([0, 50, 120]);
    expect(miniBounds(130, 50)).toEqual([0, 50, 100, 130]);
  });

  it("区間に欠けがあれば null", () => {
    const l = lap("a", [0, 1, 2, null, 4, 5]);
    expect(sectorTimes(l, [0, 2, 5])).toEqual([2, null]);
  });
});

describe("bearingAt", () => {
  it("北が 0°、東が 90°（時計回り）", () => {
    const north: Refline = { s: [0, 1, 2, 3, 4, 5, 6, 7], x: [0, 0, 0, 0, 0, 0, 0, 0], y: [0, 1, 2, 3, 4, 5, 6, 7] };
    const east: Refline = { s: [0, 1, 2, 3, 4, 5, 6, 7], x: [0, 1, 2, 3, 4, 5, 6, 7], y: [0, 0, 0, 0, 0, 0, 0, 0] };
    expect(bearingAt(north, 3)).toBeCloseTo(0, 9);
    expect(bearingAt(east, 3)).toBeCloseTo(90, 9);
  });
});

/**
 * 契約：fixtures/contract.json は analysis.export の小さいコースの書き出し（pytest の test_export_contract が、
 * 今の書き出しと一致することを確かめる）。写した処理が Python と同じ値を出すか確かめる。
 */
describe("Python の書き出しとの契約", () => {
  const { doc, expect: want } = contract as unknown as {
    doc: CourseData;
    expect: { ref: string; final_delta: Record<string, { s: number; dt: number }> };
  };

  it("checkCourse を通る", () => {
    expect(() => checkCourse(doc)).not.toThrow();
  });

  it("finalDelta(timeDelta) が data.final_delta と 1e-3 s 以内で一致する", () => {
    const ref = doc.laps.find((l) => l.label === want.ref)!;
    const got = Object.fromEntries(
      doc.laps.flatMap((l) => {
        const f = finalDelta(timeDelta(l, ref));
        return f ? [[l.label, f]] : [];
      }),
    );
    expect(Object.keys(got).sort()).toEqual(Object.keys(want.final_delta).sort());
    for (const [label, f] of Object.entries(want.final_delta)) {
      expect(got[label]!.s).toBe(f.s);
      expect(Math.abs(got[label]!.dt - f.dt)).toBeLessThanOrEqual(1e-3 + 1e-9);
    }
  });

  it("sectorTimes を区間の境界で求めると section_times と一致する", () => {
    const bounds = [...doc.sections!.map((s) => s.s_start), doc.grid_max];
    for (const l of doc.laps) {
      const got = sectorTimes(l, bounds);
      l.section_times!.forEach((v, i) => {
        if (v == null) expect(got[i]).toBeNull();
        else expect(got[i]).toBeCloseTo(v, 6);
      });
    }
  });

  it("コーナーは id で値を引ける", () => {
    const [t1, t2] = cornersOf(doc);
    expect(t1).toMatchObject({ id: "T1", side: "right" });
    expect(t2).toMatchObject({ id: "T2", side: null, s_apex: null });
    const l = doc.laps.find((x) => x.label === "racechrono rc1 #1")!;
    expect(sectionTime(doc, l, "T1")).toBe(l.section_times![1]);
    expect(l.corner_stats!["T1"]!.s_brake).toBe(15);
    expect(sectionTime(doc, l, "T9")).toBeNull();
  });
});
