# Planner AI 4.0 - ringkasan untuk pembaca yang baru bangun

Dibangun otomatis pada 2026-10-25 tanpa persetujuan di tengah jalan. Semua yang tertulis di bawah sudah dijalankan di lingkungan
Linux (Python 3.13, Node 22, Chromium); bagian yang TIDAK dijalankan ditandai jelas di bagian "Batas jujur".

## Apa yang ada sekarang

| Bagian | File | Keterangan singkat |
|---|---|---|
| 4 engine inti | `core/coordination/blackboard.py`, `core/verification/sealed_gate.py`, `core/verification/trace_slice_plugin.py`, `core/ui/` (linter CDP) | Prototipe di-audit dan diperkeras (lihat `docs/ENGINE_AUDIT.md`), bukan disalin mentah |
| Armada bicameral | `core/fleet.py` | AG1 Lawgiver, AG6 Inquisitor, AG2 DB, AG3 UI, AG4 QA bounty, AG5 API, AG7 DevOps, AG8 Arbiter (AppSec, arbiter bounty, escalation solver); 8 worker aktif, `--lead 1|6|8` memilih siapa yang memakai kuota premium; rentang port MASP 100 dev + 100 API per worker |
| Tiket Contract-First | `dag_planner.py`, `core/contract_ticket.py` | YAML bertanda tangan (sha256 atau HMAC lewat env `PLANNER_LAWGIVER_KEY`), simbol SCIP, DAG |
| Penjalan | `swarm.py`, `orchestrator.py`, `verifier.py` | AG1 stub -> AG6 segel oracle -> worker -> 12 cek verifier -> bounty AG4 -> gerbang mutasi 0,92 -> DONE/ESCALATED |
| Mutation testing | `core/verification/mutation_gate.py`, `mutation_adapters.py` | Mesin AST sendiri (semua OS), adapter Stryker (JS/TS) dan mutmut (Linux/WSL2), diuji dengan tool asli |
| Jembatan Antigravity | `core/antigravity_bridge.py` | Deteksi OS, pemetaan profil AG1..AG8 (Windows `AppData\Roaming\Antigravity<N>`, Linux `AntigravityProfiles/Account<N>`), env task yang disanitasi; TIDAK mengarang flag CLI Antigravity |
| Audit AppSec AG8 | `core/verification/appsec_audit.py` | Audit AST deterministik: injeksi, taint path, secret, konkurensi (lock, TOCTOU); hanya Python |
| Skill operator | `skills/planner-4-engine/` | `SKILL.md` + `bin/planner4` (Linux/WSL2) dan `bin/planner4.bat` (Windows) |
| Golden E2E | `evals/golden_e2e_run.py` | 7 langkah end-to-end (kontrak HMAC, vault HMAC, 2 implementer paralel, bounty AG4, mutasi, audit AG8, verifier 12 cek), exit 0 jika semua lulus; `planner4 golden` |
| Dokumen | `docs/ARCHITECTURE.md`, `docs/ENGINE_AUDIT.md`, `docs/RESEARCH_REPORT_ID.md` | Desain, audit engine, laporan riset 18 repo (bahasa Indonesia) |

Suite Planner 3.0 tetap utuh dan lulus (tidak ada regresi). Angka tes terakhir ada di `progress.md` bagian 10.

## Cara mencoba (5 menit)

```
cd planner-system-4.0
python -m unittest discover tests                  # semua tes 3.0 + 4.0
python orchestrator.py plan --root <proyek>         # lihat jadwal, worker, tier, port
python orchestrator.py run  --root <proyek> --executor command --cmd "<CLI agen Anda>" \
       --pass-env NAMA_ENV_KREDENSIAL -v
```

`--executor dry-run` (bawaan) tidak menjalankan apa pun. Placeholder perintah agen: `{prompt_file} {workspace} {model} {effort}
{worker} {ticket} {task}`. Prompt dikirim lewat file, bukan lewat string shell. Tidak ada yang menyuruh melewati prompt izin.

## Keputusan desain yang saya ambil sendiri (tolong tinjau)

1. Bobot bounty AG4 `U = 1*falsified + 2*kill_ratio - 1.5*false_positives` adalah pilihan saya; spec tidak memberi angka.
2. Mutation testing per-scope ditunda ke tahap integrasi, supaya AG4 punya kesempatan "mendapat bounty" sebelum tiket ditutup.
3. Worker bekerja di salinan workspace privat; hanya scope yang lolos yang di-merge (dengan deteksi konflik), jadi fencing
   token blackboard tidak perlu menjangkau file di disk.
4. Oracle yang sudah lulus terhadap stub dianggap hampa (VACUOUS) dan divault dihapus.
5. `INCONCLUSIVE` tidak pernah dihitung lulus; `PARTIAL_PASS` dan `SELF_PASS` tidak pernah menutup tiket.

## Batas jujur

- **Windows 11 native dan WSL2 tidak dijalankan.** Jalur Windows (taskkill, SO_EXCLUSIVEADDRUSE, nama reserved, retry os.replace)
  ditulis sesuai dokumentasi dan diuji lewat injeksi, bukan di mesin Windows. Langkah pertama di sana: `python -m unittest discover tests`.
- `swarm.py` asli Anda (`AntigravityProfiles/orchestrator/swarm.py`) tidak terjangkau dari lingkungan ini. Saya membuat versi
  yang kompatibel dengan kontrak JSON lama (`worker_results`, `orchestrator_summary`). Untuk mendelegasikan ke yang asli:
  `--executor legacy --legacy-script <path>`.
- Orchestrator diuji dengan agen palsu yang berskrip (puluhan skenario), BUKAN dengan model sungguhan. Perilaku kuota/biaya agen nyata belum terukur.
- Verifier dan gate bukan container. Untuk agen yang tidak dipercaya, jalankan di user OS terpisah atau container.
- Mutan ekuivalen tidak bisa dideteksi; ambang 0,92 plus loop bounty adalah mitigasi, bukan bukti.
- Port statis hanya mendeteksi angka literal; port hasil perhitungan tidak terlihat.
- Scanner simbol TypeScript bersifat leksikal, bukan `tsc`. Bentuk simbol SCIP untuk direktori bersarang belum dicek dengan indexer asli.
- `ast-grep` 0.51.0 dipasang lewat pip di sini, jadi 10 eval berjalan (10/10). Di mesin Anda binary itu harus ada di PATH.
- Golden run memakai agen berskrip, bukan Antigravity sungguhan: ia membuktikan pipa dan gerbang, bukan mutu model. Ia menemukan dua bug engine nyata (tiket HMAC tidak bisa didaftarkan; cek `contract` gagal setelah DONE) yang sekarang sudah diperbaiki dengan tes regresi.
- Audit AppSec bukan produk SAST: tidak ada aliran antar-fungsi; `SEC-PATH` ramai pada kode utilitas file.

## Langkah berikut yang disarankan

1. Jalankan suite di Windows 11 dan di WSL2 (ext4, bukan `/mnt/c`). 2. Satu tiket nyata dengan agen sungguhan dan baca
`.planner/escalations/`. 3. Bandingkan laporan `--executor legacy` dengan swarm.py asli. 4. Bungkus verifier dalam container
bila agen tidak dipercaya.
