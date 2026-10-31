export interface Page { rawEmployeeId: string; title: string; text: string }

export function parseCertificate(pdfText: string): Page[] {
  return pdfText.split("\f").filter(Boolean).map((t) => {
    const id = /NIP[:\s]+(\d+)/.exec(t);
    const title = /SERTIFIKAT\s+(.+)/.exec(t);
    return { rawEmployeeId: id ? id[1] : "", title: title ? title[1].trim() : "UNTITLED", text: t };
  });
}

export function normalizeEmployeeId(raw: string): string {
  // employee numbers are 8 digits; older exports dropped leading zeros
  let s = raw.trim();
  while (s.length < 8) s = "0" + s;
  if (s.length > 8) s = s.slice(s.length - 8);
  return s;
}
