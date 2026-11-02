// Headline numbers for the compat page. Everything here is derived from the
// data file, so the page cannot claim more than the data says.

import type { CompatData, IslandStatus, PackagePlane, Status, SupportRow } from "./compat";

export interface CategorySummary {
  key: string;
  label: string;
  total: number;
  full: number;
  partial: number;
  none: number;
}

export function getHeadlineStat(data: CompatData) {
  const rows = [...data.builtins, ...data.stdlib];
  let supported = 0;
  for (const row of rows) {
    if (row.status === "full" || row.status === "partial") supported += 1;
  }
  const total = rows.length;
  return {
    supported,
    total,
    percent: total === 0 ? 0 : Math.round((supported / total) * 100),
  };
}

export function getCategorySummaries(data: CompatData): CategorySummary[] {
  const groups: [string, string, SupportRow[]][] = [
    ["builtins", "Builtins", data.builtins],
    ["stdlib", "Standard library", data.stdlib],
    ["packages", "Pinned packages", data.packages.map(toSupportRow)],
  ];
  return groups.map(([key, label, rows]) => {
    let full = 0;
    let partial = 0;
    let none = 0;
    for (const row of rows) {
      if (row.status === "full") full += 1;
      else if (row.status === "partial") partial += 1;
      else none += 1;
    }
    return { key, label, total: rows.length, full, partial, none };
  });
}

function toSupportRow(pkg: CompatData["packages"][number]): SupportRow {
  return {
    id: `package.${pkg.name}`,
    kind: "builtin",
    label: pkg.name,
    names: [pkg.import_name],
    status: pkg.status,
    count: pkg.definitions,
    unsupported: [],
    fences: pkg.codes.map((c) => c.code),
    note: "",
  };
}

export function getPackageCount(data: CompatData): number {
  return data.packages.length;
}

// The island headline: how many pinned packages were measured to run on the
// island tier. The denominator is the measured set, not the corpus, so a
// corpus the probe has not covered yet cannot inflate the number.
export function getIslandHeadline(data: CompatData) {
  const measured = data.island.packages;
  const runs = data.island.runs;
  return {
    measured,
    runs,
    percent: measured === 0 ? 0 : Math.round((runs / measured) * 100),
  };
}

// The island verdict counts over the package rows.
export function getIslandCounts(data: CompatData): Record<IslandStatus, number> {
  const counts: Record<IslandStatus, number> = { runs: 0, blocked: 0, unmeasured: 0 };
  for (const row of data.packages) counts[row.island] += 1;
  return counts;
}

// The plane share over the whole corpus: how many definitions lower
// natively, how many route to the island and how many stay blocked. The
// denominator is the definitions the measurement placed.
export function getPlaneShare(data: CompatData) {
  const totals: PackagePlane = { definitions: 0, native: 0, island: 0, blocked: 0 };
  for (const row of data.packages) {
    totals.definitions += row.plane.definitions;
    totals.native += row.plane.native;
    totals.island += row.plane.island;
    totals.blocked += row.plane.blocked;
  }
  const denominator = totals.native + totals.island + totals.blocked;
  const share = (value: number) => (denominator === 0 ? 0 : Math.round((value / denominator) * 100));
  return {
    ...totals,
    nativeShare: share(totals.native),
    islandShare: share(totals.island),
    blockedShare: share(totals.blocked),
  };
}

export function statusPercent(rows: { status: Status }[], status: Status): number {
  if (rows.length === 0) return 0;
  const count = rows.filter((row) => row.status === status).length;
  return Math.round((count / rows.length) * 100);
}
