import { query } from "../db";
import { parseCertificate, normalizeEmployeeId } from "../core/parser";
import { canDelete } from "./auth";

export async function listCerts(req: any, res: any) {
  const rows = await query("SELECT id, employee_id, file_name FROM certificates ORDER BY id DESC LIMIT 100");
  res.json(rows);
}

export async function handleSliceCert(req: any, res: any) {
  const pages = parseCertificate(req.body.pdfText);
  const saved = [];
  for (const p of pages) {
    const empId = normalizeEmployeeId(p.rawEmployeeId);
    const emp = await query("SELECT id FROM employees WHERE employee_no = $1", [empId]);
    if (!emp.length) {
      await query("INSERT INTO logs (level, msg) VALUES ('warn', $1)", ["unmatched " + p.rawEmployeeId]);
      continue;
    }
    const name = empId + "_" + p.title.replace(/\s+/g, "_") + ".pdf";
    await query("INSERT INTO certificates (employee_id, file_name) VALUES ($1, $2)", [emp[0].id, name]);
    saved.push(name);
  }
  res.json({ saved });
}

export async function removeCert(req: any, res: any) {
  if (!canDelete(req.user)) return res.status(403).end();
  await query("DELETE FROM certificates WHERE id = $1", [req.params.id]);
  res.status(204).end();
}
