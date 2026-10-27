// Headline numbers for the compat page. Everything here is derived from the
// data file, so the page cannot claim more than the data says.

import type { CompatData, Status, SupportRow } from "./compat";

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
    ["packages", "PyPI top 100", data.packages.map(toSupportRow)],
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

export function statusPercent(rows: { status: Status }[], status: Status): number {
  if (rows.length === 0) return 0;
  const count = rows.filter((row) => row.status === status).length;
  return Math.round((count / rows.length) * 100);
}
