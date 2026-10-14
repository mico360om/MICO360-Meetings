/** Calendar dates as ISO strings "YYYY-MM-DD" (no time zone surprises). */
export type ISODate = string;

const pad = (n: number, w = 2) => String(n).padStart(w, "0");
const isLeap = (y: number) => (y % 4 === 0 && y % 100 !== 0) || y % 400 === 0;
const daysIn = (y: number, m: number) => [31, isLeap(y) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1];

export function makeDate(y: number, m: number, d: number): ISODate | null {
  if (!Number.isInteger(y) || !Number.isInteger(m) || !Number.isInteger(d)) return null;
  if (y < 1 || y > 9999 || m < 1 || m > 12 || d < 1 || d > daysIn(y, m)) return null;
  return `${pad(y, 4)}-${pad(m)}-${pad(d)}`;
}

function toUtc(iso: ISODate): Date {
  const [y, m, d] = iso.split("-").map(Number);
  const t = new Date(0);
  t.setUTCFullYear(y, m - 1, d);
  return t;
}

const fromUtc = (t: Date): ISODate => `${pad(t.getUTCFullYear(), 4)}-${pad(t.getUTCMonth() + 1)}-${pad(t.getUTCDate())}`;

export function addDays(iso: ISODate, n: number): ISODate {
  const t = toUtc(iso);
  t.setUTCDate(t.getUTCDate() + n);
  return fromUtc(t);
}

/** Monday = 0 … Sunday = 6 (Python date.weekday()). */
export function weekday(iso: ISODate): number {
  return (toUtc(iso).getUTCDay() + 6) % 7;
}

export function todayLocal(now: Date = new Date()): ISODate {
  return makeDate(now.getFullYear(), now.getMonth() + 1, now.getDate())!;
}

export function localDateOf(ms: number): ISODate {
  return todayLocal(new Date(ms));
}

/** Python date.fromisoformat for the forms the app meets: YYYY-MM-DD, YYYYMMDD. */
export function fromIso(s: string): ISODate | null {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(s) ?? /^(\d{4})(\d{2})(\d{2})$/.exec(s);
  return m ? makeDate(+m[1], +m[2], +m[3]) : null;
}
