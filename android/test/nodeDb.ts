import { DatabaseSync } from "node:sqlite";
import type { Db, SqlParam } from "../src/data/db";

/** The Db interface over Node's built-in SQLite (rows copied to plain objects). */
export function nodeDb(): Db {
  const db = new DatabaseSync(":memory:");
  const plain = <T>(r: unknown) => (r ? ({ ...(r as object) } as T) : null);
  return {
    async execAsync(sql) { db.exec(sql); },
    async runAsync(sql, params: SqlParam[] = []) {
      const r = db.prepare(sql).run(...params);
      return { changes: Number(r.changes), lastInsertRowId: Number(r.lastInsertRowid) };
    },
    async getAllAsync<T>(sql: string, params: SqlParam[] = []) { return db.prepare(sql).all(...params).map((r) => plain<T>(r)!); },
    async getFirstAsync<T>(sql: string, params: SqlParam[] = []) { return plain<T>(db.prepare(sql).get(...params)); },
    async withTransactionAsync(fn) {
      db.exec("BEGIN");
      try { await fn(); db.exec("COMMIT"); } catch (e) { db.exec("ROLLBACK"); throw e; }
    },
  };
}
