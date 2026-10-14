/** The app's database and a tiny change feed so every screen shows fresh data. */
import { type SQLiteDatabase, SQLiteProvider, useSQLiteContext } from "expo-sqlite";
import { useFocusEffect } from "expo-router";
import { type ReactNode, useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Db } from "../data/db";
import { migrate, seed } from "../data/schema";
import { type AppSettings, DEFAULT_SETTINGS, getSettings, setSetting } from "../data/settings";

/** expo-sqlite behind the app's Db interface (the same one the tests back with node:sqlite). */
export function asDb(db: SQLiteDatabase): Db {
  return {
    execAsync: (sql) => db.execAsync(sql),
    runAsync: (sql, params = []) => db.runAsync(sql, params),
    getAllAsync: (sql, params = []) => db.getAllAsync(sql, params),
    getFirstAsync: (sql, params = []) => db.getFirstAsync(sql, params),
    withTransactionAsync: (fn) => db.withTransactionAsync(fn),
  };
}

export function DbProvider({ children }: { children: ReactNode }) {
  return (
    <SQLiteProvider databaseName="mico360-meetings.db" onInit={async (db) => {
      await db.execAsync("PRAGMA journal_mode = WAL");
      await migrate(asDb(db));
      await seed(asDb(db));
    }}>
      {children}
    </SQLiteProvider>
  );
}

export function useDb(): Db {
  const db = useSQLiteContext();
  return useMemo(() => asDb(db), [db]);
}

const listeners = new Set<() => void>();

/** Tell every open screen that stored data changed. */
export function notifyChanged(): void {
  for (const l of [...listeners]) l();
}

/** Run `fn` now, whenever the screen regains focus, and after any change. */
export function useQuery<T>(fn: (db: Db) => Promise<T>, deps: unknown[] = []): {
  data: T | undefined; error: string; loading: boolean; reload: () => void;
} {
  const db = useDb();
  const [data, setData] = useState<T>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const reload = useCallback(() => {
    const mine = ++seq.current;
    setLoading(true);
    fn(db).then((v) => { if (mine === seq.current) { setData(v); setError(""); } })
      .catch((e: unknown) => { if (mine === seq.current) setError(e instanceof Error ? e.message : String(e)); })
      .finally(() => { if (mine === seq.current) setLoading(false); });
  }, [db, ...deps]);
  useFocusEffect(reload);
  useEffect(() => { listeners.add(reload); return () => { listeners.delete(reload); }; }, [reload]);
  return { data, error, loading, reload };
}

export function useSettings(): { settings: AppSettings; update: <K extends keyof AppSettings>(k: K, v: AppSettings[K]) => Promise<void> } {
  const db = useDb();
  const { data } = useQuery(getSettings);
  const update = useCallback(async <K extends keyof AppSettings>(k: K, v: AppSettings[K]) => {
    await setSetting(db, k, v);
    notifyChanged();
  }, [db]);
  return { settings: data ?? DEFAULT_SETTINGS, update };
}

export const errorText = (e: unknown) => (e instanceof Error ? e.message : String(e));
