import { describe, expect, it } from "vitest";
import type { CourseData, Lap } from "./data";
import { hasOtherBest } from "./sections";

function lap(label: string, source: string, lap_valid: boolean, section_times: (number | null)[] | null): Lap {
  return { label, source, lap_valid, section_times } as Lap;
}

function course(laps: Lap[]): CourseData {
  return { laps } as CourseData;
}

describe("hasOtherBest", () => {
  it("同じソースの有効な周が自分だけなら false", () => {
    const ac = lap("ac", "ac", true, [1, 2]);
    const rc = lap("rc", "racechrono", true, [1, 2]);
    expect(hasOtherBest(course([ac, rc]), ac)).toBe(false);
  });

  it("ほかの有効な周に区間タイムがなければ false", () => {
    const a = lap("a", "ac", true, [1, 2]);
    const b = lap("b", "ac", true, null);
    expect(hasOtherBest(course([a, b]), a)).toBe(false);
  });

  it("無効な周を比べるときは、同じソースの有効な周があれば true", () => {
    const a = lap("a", "ac", false, [1, 2]);
    const b = lap("b", "ac", true, [1, 2]);
    expect(hasOtherBest(course([a, b]), a)).toBe(true);
  });
});
