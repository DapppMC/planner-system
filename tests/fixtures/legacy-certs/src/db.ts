import { Pool } from "pg";
const pool = new Pool({ connectionString: process.env.DATABASE_URL });
export async function query(sql: string, params: any[] = []) {
  const r = await pool.query(sql, params);
  return r.rows;
}
