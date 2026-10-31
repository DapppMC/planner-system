const rows = await db.query("SELECT * FROM certificates WHERE id = $1", [id]);
await db.execute("DELETE FROM logs");
const n = pool.query(`SELECT count(*) FROM employees`);
console.log("no sql here");
