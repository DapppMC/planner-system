"""M4 tests: contract-first ticket schema, signatures, scope checks, flat conversion, blackboard registration."""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from core import contract_ticket as ct  # noqa: E402
from core import miniyaml  # noqa: E402
from core.coordination.blackboard import Board  # noqa: E402
import ticketc  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "contracts" / "escrow.yaml"


def fresh(signed=True):
    doc = ct.load_ticket(FIXTURE)
    ct.lock_symbols(doc)
    if signed:
        ct.sign(doc)
    return doc


def errors(doc, **kw):
    return ct.validate(doc, **kw).errors


class SchemaValid(unittest.TestCase):
    def test_spec_fixture_is_valid_and_clean(self):
        rep = ct.validate(fresh())
        self.assertEqual(rep.errors, [])
        self.assertEqual(rep.warnings, [])

    def test_unsigned_is_an_error_unless_planning(self):
        doc = fresh(signed=False)
        self.assertTrue(any("unsigned" in e for e in errors(doc)))
        rep = ct.validate(doc, allow_unsigned=True)
        self.assertEqual(rep.errors, [])
        self.assertTrue(any("unsigned" in w for w in rep.warnings))

    def test_unknown_keys_are_errors_everywhere(self):
        for mutate, needle in [
            (lambda d: d.update(extra=1), "unknown top-level key"),
            (lambda d: d["ticket_meta"].update(touchs=[]), "ticket_meta: unknown key"),
            (lambda d: d["worker_scopes"]["worker_ui"].update(zeta=1), "worker_ui: unknown key"),
            (lambda d: d["worker_scopes"]["worker_ui"]["kinematic_spec"].update(damping=1), "kinematic_spec: unknown key"),
            (lambda d: d["worker_scopes"]["worker_qa_guard"]["mutation_gate"].update(kill=1), "mutation_gate: unknown key"),
            (lambda d: d["worker_scopes"]["worker_backend"]["formal_invariants"].update(lib="x"), "formal_invariants: unknown key"),
            (lambda d: d["worker_scopes"].update(worker_ghost={"worker_id": "AG9", "allowed_paths": ["x"]}), "unknown scope"),
            (lambda d: d["shared_contracts"].update(other=1), "shared_contracts: unknown key"),
        ]:
            d = fresh(signed=False)
            mutate(d)
            errs = errors(d, check_signature=False)
            self.assertTrue(any(needle in e for e in errs), f"{needle}: {errs}")

    def test_meta_rules(self):
        cases = {
            "id": (lambda d: d["ticket_meta"].update(id="x"), "ticket_meta.id"),
            "id chars": (lambda d: d["ticket_meta"].update(id="bad id with spaces"), "ticket_meta.id"),
            "epoch 0": (lambda d: d["ticket_meta"].update(epoch=0), "epoch"),
            "epoch bool": (lambda d: d["ticket_meta"].update(epoch=True), "epoch"),
            "epoch str": (lambda d: d["ticket_meta"].update(epoch="14"), "epoch"),
            "no touches": (lambda d: d["ticket_meta"].update(touches=[]), "must not be empty"),
            "dup touches": (lambda d: d["ticket_meta"].update(touches=d["ticket_meta"]["touches"] + [d["ticket_meta"]["touches"][0]]), "duplicate"),
            "traversal": (lambda d: d["ticket_meta"]["touches"].append("../etc/passwd"), "parent traversal"),
            "absolute": (lambda d: d["ticket_meta"]["touches"].append("/etc/passwd"), "absolute"),
            "drive": (lambda d: d["ticket_meta"]["touches"].append("C:\\Windows\\x"), "absolute"),
            "git": (lambda d: d["ticket_meta"]["touches"].append(".git/config"), "protected"),
            "env": (lambda d: d["ticket_meta"]["touches"].append("apps/web/.env"), "protected"),
            "pem": (lambda d: d["ticket_meta"]["touches"].append("certs/server.pem"), "protected"),
            "planner": (lambda d: d["ticket_meta"]["touches"].append(".planner/board.db"), "protected"),
            "too broad": (lambda d: d["ticket_meta"]["touches"].append("**"), "too broad"),
            "self dep": (lambda d: d["ticket_meta"].update(depends_on=[d["ticket_meta"]["id"]]), "itself"),
            "empty goal": (lambda d: d["ticket_meta"].update(goal=" "), "goal"),
        }
        for name, (mutate, needle) in cases.items():
            d = fresh(signed=False)
            mutate(d)
            errs = errors(d, check_signature=False)
            self.assertTrue(any(needle in e for e in errs), f"{name}: {errs}")

    def test_scope_rules(self):
        cases = {
            "wrong worker id": (lambda d: d["worker_scopes"]["worker_db"].update(worker_id="AG5"), "worker_id must be 'AG2'"),
            "no allowed": (lambda d: d["worker_scopes"]["worker_db"].pop("allowed_paths"), "allowed_paths is required"),
            "too broad": (lambda d: d["worker_scopes"]["worker_db"].update(allowed_paths=["**"]), "too broad"),
            "backend bad inv": (lambda d: d["worker_scopes"]["worker_backend"]["formal_invariants"].update(properties=["no id here"]), "INV-"),
            "backend dup inv": (lambda d: d["worker_scopes"]["worker_backend"]["formal_invariants"].update(properties=["INV-01: a", "INV-01: b"]), "duplicate invariant"),
            "backend no engine": (lambda d: d["worker_scopes"]["worker_backend"]["formal_invariants"].pop("engine"), "engine is required"),
            "backend inv list": (lambda d: d["worker_scopes"]["worker_backend"].update(formal_invariants=["INV-01: a"]), "must be a mapping"),
            "zeta 0": (lambda d: d["worker_scopes"]["worker_ui"]["kinematic_spec"].update(spring_damping_zeta=0), "spring_damping_zeta"),
            "zeta big": (lambda d: d["worker_scopes"]["worker_ui"]["kinematic_spec"].update(spring_damping_zeta=5), "spring_damping_zeta"),
            "zeta bool": (lambda d: d["worker_scopes"]["worker_ui"]["kinematic_spec"].update(spring_damping_zeta=True), "spring_damping_zeta"),
            "zeta nan": (lambda d: d["worker_scopes"]["worker_ui"]["kinematic_spec"].update(spring_damping_zeta=float("nan")), "spring_damping_zeta"),
            "lc low": (lambda d: d["worker_scopes"]["worker_ui"]["kinematic_spec"].update(target_apca_lc=5), "target_apca_lc"),
            "lc high": (lambda d: d["worker_scopes"]["worker_ui"]["kinematic_spec"].update(target_apca_lc=200), "target_apca_lc"),
            "trim list": (lambda d: d["worker_scopes"]["worker_ui"]["kinematic_spec"].update(optical_trim=[1]), "optical_trim"),
            "fsm empty": (lambda d: d["worker_scopes"]["worker_ui"].update(mandatory_fsm=""), "mandatory_fsm"),
            "qa role": (lambda d: d["worker_scopes"]["worker_qa_guard"].update(role="FRIEND"), "ADVERSARIAL_BOUNTY"),
            "qa no gate": (lambda d: d["worker_scopes"]["worker_qa_guard"].pop("mutation_gate"), "mutation_gate is required"),
            "qa score 0": (lambda d: d["worker_scopes"]["worker_qa_guard"]["mutation_gate"].update(min_kill_score=0), "min_kill_score"),
            "qa score >1": (lambda d: d["worker_scopes"]["worker_qa_guard"]["mutation_gate"].update(min_kill_score=1.5), "min_kill_score"),
            "qa diff flag": (lambda d: d["worker_scopes"]["worker_qa_guard"]["mutation_gate"].update(target_diff_only="yes"), "target_diff_only"),
            "forbidden_calls type": (lambda d: d["worker_scopes"]["worker_qa_guard"].update(forbidden_calls="jest.mock"), "forbidden_calls"),
            "scope not map": (lambda d: d["worker_scopes"].update(worker_db=[1]), "must be a mapping"),
            "no scopes": (lambda d: d.update(worker_scopes={}), "worker_scopes"),
        }
        for name, (mutate, needle) in cases.items():
            d = fresh(signed=False)
            mutate(d)
            errs = errors(d, check_signature=False)
            self.assertTrue(any(needle in e for e in errs), f"{name}: {errs}")

    def test_security_scope_belongs_to_ag8(self):
        self.assertEqual(ct.SCOPE_WORKER["worker_security"], "AG8")
        self.assertEqual(ct.WORKER_SCOPE["AG8"], "worker_security")
        d = fresh(signed=False)
        d["worker_scopes"]["worker_security"] = {"worker_id": "AG8", "allowed_paths": ["apps/api/security/**"],
                                                 "forbidden_calls": ["eval"],
                                                 "formal_invariants": {"engine": "hypothesis", "properties": ["INV-07: no user input reaches a shell"]}}
        self.assertEqual(errors(d, check_signature=False), [])
        d["worker_scopes"]["worker_security"]["worker_id"] = "AG5"
        self.assertTrue(any("worker_id must be 'AG8'" in e for e in errors(d, check_signature=False)))
        d["worker_scopes"]["worker_security"].update(worker_id="AG8", zeta=1)
        self.assertTrue(any("worker_security: unknown key" in e for e in errors(d, check_signature=False)))
        d["worker_scopes"]["worker_security"].pop("zeta")
        d["worker_scopes"]["worker_security"]["formal_invariants"]["properties"] = ["no id"]
        self.assertTrue(any("INV-" in e for e in errors(d, check_signature=False)))

    def test_security_scope_cannot_overlap_the_backend_scope(self):
        d = fresh(signed=False)
        d["worker_scopes"]["worker_security"] = {"worker_id": "AG8", "allowed_paths": ["packages/api/src/domain/escrow/**"]}
        self.assertTrue(errors(d, check_signature=False))

    def test_weak_gate_and_test_paths_warn(self):
        d = fresh(signed=False)
        d["worker_scopes"]["worker_qa_guard"]["mutation_gate"]["min_kill_score"] = 0.3
        d["worker_scopes"]["worker_ui"]["allowed_paths"].append("apps/web/src/__tests__/x.test.tsx")
        d["worker_scopes"]["worker_backend"]["formal_invariants"]["engine"] = "homemade"
        rep = ct.validate(d, check_signature=False)
        self.assertEqual(rep.errors, [])
        text = " | ".join(rep.warnings)
        self.assertIn("below 0.5", text)
        self.assertIn("looks like a test path", text)
        self.assertIn("homemade", text)

    def test_overlapping_write_scopes_are_rejected(self):
        d = fresh(signed=False)
        d["worker_scopes"]["worker_backend"]["allowed_paths"].append("packages/db/migrations/*.sql")
        errs = errors(d, check_signature=False)
        self.assertTrue(any("write scopes overlap" in e for e in errs), errs)
        d = fresh(signed=False)
        d["worker_scopes"]["worker_ui"]["allowed_paths"] = ["apps/web/**"]
        d["worker_scopes"]["worker_qa_guard"]["allowed_paths"] = ["apps/web/src/features/**/*.spec.ts"]
        self.assertTrue(any("write scopes overlap" in e for e in errors(d, check_signature=False)))

    def test_touch_nobody_may_write_is_rejected(self):
        d = fresh(signed=False)
        d["ticket_meta"]["touches"].append("packages/api/src/domain/escrow/orphan.ts")
        errs = errors(d, check_signature=False)
        self.assertTrue(any("nobody may write it" in e for e in errs), errs)

    def test_scope_without_lease_warns(self):
        d = fresh(signed=False)
        d["worker_scopes"]["worker_ui"]["allowed_paths"].append("apps/web/src/other/*.tsx")
        rep = ct.validate(d, check_signature=False)
        self.assertEqual(rep.errors, [])
        self.assertTrue(any("overlaps no entry" in w for w in rep.warnings))

    def test_glob_touches_are_covered_by_overlap(self):
        d = fresh(signed=False)
        d["ticket_meta"]["touches"] = ["packages/db/migrations/*.sql", "packages/api/src/domain/escrow/*.ts", "apps/web/src/features/escrow/*.tsx"]
        self.assertEqual(errors(d, check_signature=False), [])

    def test_not_a_mapping(self):
        for bad in (None, [], "x", 3):
            self.assertTrue(errors(bad))
        self.assertTrue(errors({"ticket_meta": {}, "worker_scopes": []}, check_signature=False))

    def test_qa_only_ticket_warns(self):
        d = fresh(signed=False)
        for k in ("worker_db", "worker_backend", "worker_ui"):
            d["worker_scopes"].pop(k)
        d["ticket_meta"]["touches"] = ["packages/api/tests/property/a.spec.ts"]
        rep = ct.validate(d, check_signature=False)
        self.assertEqual(rep.errors, [])
        self.assertTrue(any("only worker_qa_guard" in w for w in rep.warnings))


class Symbols(unittest.TestCase):
    def test_symbol_validation(self):
        for mutate, needle in [
            (lambda d: d["shared_contracts"]["scip_symbols"][0].update(symbol="garbage"), "scip_symbols[0].symbol"),
            (lambda d: d["shared_contracts"]["scip_symbols"][0].update(symbol="local 1"), "local symbols"),
            (lambda d: d["shared_contracts"]["scip_symbols"][0].update(signature=""), "signature"),
            (lambda d: d["shared_contracts"]["scip_symbols"][0].update(extra=1), "unknown key"),
            (lambda d: d["shared_contracts"]["scip_symbols"].append(dict(d["shared_contracts"]["scip_symbols"][0])), "duplicates"),
            (lambda d: d["shared_contracts"].update(scip_symbols="x"), "must be a list"),
            (lambda d: d["shared_contracts"]["scip_symbols"][0].update(signature="x" * 20001), "longer than"),
        ]:
            d = fresh(signed=False)
            mutate(d)
            errs = errors(d, check_signature=False)
            self.assertTrue(any(needle in e for e in errs), f"{needle}: {errs}")

    def test_lock_mismatch_is_detected(self):
        d = fresh(signed=False)
        d["shared_contracts"]["scip_symbols"][0]["signature"] = d["shared_contracts"]["scip_symbols"][0]["signature"].replace("string", "number", 1)
        errs = errors(d, check_signature=False)
        self.assertTrue(any("lock mismatch" in e for e in errs), errs)

    def test_lock_is_whitespace_insensitive(self):
        d = fresh(signed=False)
        d["shared_contracts"]["scip_symbols"][0]["signature"] += "\n\n"
        self.assertEqual(errors(d, check_signature=False), [])

    def test_drift_detection(self):
        d = fresh()
        sym = d["shared_contracts"]["scip_symbols"][0]["symbol"]
        sig = d["shared_contracts"]["scip_symbols"][0]["signature"]
        self.assertEqual(ct.check_symbol_drift(d, {sym: sig}), [])
        self.assertTrue(any("drifted" in p for p in ct.check_symbol_drift(d, {sym: sig.replace("bigint", "number")})))
        self.assertTrue(any("not found" in p for p in ct.check_symbol_drift(d, {})))
        self.assertEqual(list(ct.symbol_locks(d)), [sym])

    def test_wrong_kind_warns_not_errors(self):
        d = fresh(signed=False)
        d["shared_contracts"]["scip_symbols"][0]["symbol"] = "scip-typescript npm @planner/core 1.0.0 domain/escrow/SettlementCommand."
        rep = ct.validate(d, check_signature=False)
        self.assertEqual(rep.errors, [])
        self.assertTrue(any("types end in '#'" in w for w in rep.warnings))


class Signatures(unittest.TestCase):
    def test_sign_and_verify_roundtrip(self):
        d = fresh()
        self.assertEqual(ct.verify_signature(d), (True, ""))
        self.assertTrue(d["ticket_meta"]["lawgiver_signature"].startswith("sha256:"))

    def test_any_semantic_edit_breaks_the_signature(self):
        base = fresh()
        edits = [
            lambda d: d["ticket_meta"].update(epoch=15),
            lambda d: d["ticket_meta"]["touches"].append("apps/web/src/features/escrow/Other.tsx"),
            lambda d: d["worker_scopes"]["worker_qa_guard"]["mutation_gate"].update(min_kill_score=0.5),
            lambda d: d["worker_scopes"]["worker_ui"]["kinematic_spec"].update(spring_damping_zeta=0.5),
            lambda d: d["worker_scopes"]["worker_qa_guard"]["forbidden_calls"].remove("vi.mock"),
            lambda d: d["shared_contracts"]["scip_symbols"][0].update(signature="export interface SettlementCommand {}"),
            lambda d: d["worker_scopes"]["worker_backend"]["formal_invariants"]["properties"].pop(),
            lambda d: d["worker_scopes"].pop("worker_db"),
        ]
        for i, e in enumerate(edits):
            d = copy.deepcopy(base)
            e(d)
            ok, why = ct.verify_signature(d)
            self.assertFalse(ok, f"edit {i} was not detected")
            self.assertIn("mismatch", why)

    def test_cosmetic_changes_keep_the_signature(self):
        base = fresh()
        d = copy.deepcopy(base)
        d["worker_scopes"]["worker_ui"]["kinematic_spec"]["target_apca_lc"] = 75  # 75.0 == 75
        d["shared_contracts"]["scip_symbols"][0]["signature"] = d["shared_contracts"]["scip_symbols"][0]["signature"].rstrip("\n").replace("\n", "\r\n")
        self.assertEqual(ct.verify_signature(d), (True, ""))
        reordered = {k: base[k] for k in reversed(list(base))}
        self.assertEqual(ct.verify_signature(reordered), (True, ""))

    def test_yaml_roundtrip_keeps_signature_valid(self):
        d = fresh()
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / "a" / "x.yaml"
            ct.save_ticket(p, d)
            back = ct.load_ticket(p)
        self.assertEqual(back, d)
        self.assertEqual(ct.verify_signature(back), (True, ""))
        self.assertEqual(ct.validate(back).errors, [])

    def test_hmac_requires_the_key(self):
        d = fresh(signed=False)
        ct.sign(d, key=b"k1")
        self.assertTrue(d["ticket_meta"]["lawgiver_signature"].startswith("hmac-sha256:"))
        self.assertEqual(ct.verify_signature(d, b"k1"), (True, ""))
        ok, why = ct.verify_signature(d, b"other")
        self.assertFalse(ok)
        self.assertIn("HMAC mismatch", why)
        ok, why = ct.verify_signature(d, None)
        self.assertFalse(ok)
        self.assertIn("no key", why)
        self.assertEqual(ct.validate(d, key=b"k1").errors, [])
        self.assertTrue(ct.validate(d, key=b"x").errors)

    def test_plain_sha_is_rejected_when_hmac_is_required(self):
        ok, why = ct.verify_signature(fresh(), b"k", require_hmac=True)
        self.assertFalse(ok)
        self.assertIn("HMAC signature is required", why)

    def test_malformed_signatures(self):
        for bad in ("", "sha256:abc", "md5:" + "0" * 32, "sha256:" + "G" * 64, "sha256:7f8a9b...", 5):
            d = fresh()
            d["ticket_meta"]["lawgiver_signature"] = bad
            ok, _ = ct.verify_signature(d)
            self.assertFalse(ok, repr(bad))

    def test_signature_is_unicode_normalised(self):
        d = fresh(signed=False)
        d["ticket_meta"]["goal"] = "caf\u00e9"
        ct.sign(d)
        d2 = copy.deepcopy(d)
        d2["ticket_meta"]["goal"] = "cafe\u0301"
        self.assertEqual(ct.verify_signature(d2), (True, ""))

    def test_key_from_env(self):
        os.environ["PLANNER_TEST_KEY"] = "abc"
        try:
            self.assertEqual(ct.key_from_env("PLANNER_TEST_KEY"), b"abc")
        finally:
            del os.environ["PLANNER_TEST_KEY"]
        self.assertIsNone(ct.key_from_env("PLANNER_TEST_KEY"))


class Scope(unittest.TestCase):
    def test_check_scope(self):
        d = fresh()
        ui = "apps/web/src/features/escrow/SettlementDrawer.tsx"
        self.assertEqual(ct.check_scope(d, "worker_ui", [ui]), [])
        out = ct.check_scope(d, "worker_ui", [ui, "packages/db/migrations/20261025_escrow.sql", "README.md", ".env", ".planner/board.db", "../x", "C:\\a"])
        text = "\n".join(out)
        self.assertIn("migrations/20261025_escrow.sql: another worker's scope", text)
        self.assertIn("README.md: outside the contract", text)
        self.assertIn(".env: protected location", text)
        self.assertIn(".planner/board.db: protected location", text)
        self.assertEqual(len(out), 6)

    def test_case_and_separator_variants_do_not_escape(self):
        d = fresh()
        self.assertEqual(ct.check_scope(d, "worker_ui", ["APPS\\Web\\src\\features\\escrow\\SettlementDrawer.tsx"]), [])
        self.assertTrue(ct.check_scope(d, "worker_ui", ["apps/web/src/features/escrow/SettlementDrawer.tsx/../../../../x"]))

    def test_qa_cannot_touch_production_and_vice_versa(self):
        d = fresh()
        self.assertTrue(ct.check_scope(d, "worker_qa_guard", ["packages/api/src/domain/escrow/settle.pure.ts"]))
        self.assertTrue(ct.check_scope(d, "worker_backend", ["packages/api/tests/property/a.spec.ts"]))
        self.assertEqual(ct.check_scope(d, "worker_qa_guard", ["packages/api/tests/property/a.spec.ts"]), [])

    def test_helpers(self):
        d = fresh()
        self.assertEqual(ct.scope_for(d, "ag3"), "worker_ui")
        self.assertIsNone(ct.scope_for(d, "AG7"))
        self.assertIsNone(ct.scope_for(d, "AG1"))
        self.assertIn("vi.mock", ct.forbidden_calls(d, "worker_qa_guard"))
        self.assertEqual(ct.forbidden_calls(d, "worker_ui"), [])


class FlatTickets(unittest.TestCase):
    def test_flat_tickets_satisfy_ticketc(self):
        d = fresh()
        for scope in d["worker_scopes"]:
            flat = ct.to_flat(d, scope)
            text = ct.flat_to_yaml(flat)
            parsed = ticketc.parse_yaml(text)
            self.assertEqual(ticketc.validate_data(parsed, str(ROOT)), [], scope)
            self.assertEqual(parsed["allowed_paths"], d["worker_scopes"][scope]["allowed_paths"])
            self.assertTrue(parsed["id"].endswith(ct.SCOPE_WORKER[scope]))

    def test_flat_forbids_other_scopes_and_protects_qa_tests(self):
        d = fresh()
        ui = ct.to_flat(d, "worker_ui")
        self.assertIn("packages/db/migrations/*.sql", ui["forbidden_paths"])
        self.assertIn(".git/**", ui["forbidden_paths"])
        self.assertEqual(ui["protected_tests"], ["packages/api/tests/property/*.spec.ts"])
        qa = ct.to_flat(d, "worker_qa_guard")
        self.assertEqual(qa["protected_tests"], [])
        self.assertTrue(ticketc.matches("packages/api/src/domain/escrow/settle.pure.ts", qa["forbidden_paths"]))

    def test_frozen_and_acceptance_overrides(self):
        d = fresh()
        flat = ct.to_flat(d, "worker_db", frozen={"a.sql": "ab" * 32}, acceptance=["pytest -q"])
        parsed = ticketc.parse_yaml(ct.flat_to_yaml(flat))
        self.assertEqual(parsed["frozen"], {"a.sql": "ab" * 32})
        self.assertEqual(parsed["acceptance"], ["pytest -q"])

    def test_special_characters_survive(self):
        flat = {"id": 'T:"1"', "goal": "say \"hi\": # not a comment", "frozen": {}, "allowed_paths": ["a b/*.py"], "forbidden_paths": [], "protected_tests": [],
                "acceptance": ["echo 'x' # y"], "stop_if": ["unicode \u2713"]}
        parsed = ticketc.parse_yaml(ct.flat_to_yaml(flat))
        self.assertEqual(parsed["goal"], flat["goal"])
        self.assertEqual(parsed["acceptance"], flat["acceptance"])


class BoardRegistration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ct_board_")
        self.board = Board(os.path.join(self.tmp, "board.db"))

    def tearDown(self):
        self.board.close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_register_and_verify(self):
        d = fresh()
        row = ct.register_contract(self.board, d)
        self.assertEqual(row["state"], "CONTRACTED")
        self.assertEqual(row["epoch"], 14)
        self.assertEqual(ct.verify_against_board(self.board, d), (True, ""))
        ev = [e for e in self.board.events_for(d["ticket_meta"]["id"]) if e["kind"] == "contract_signed"]
        self.assertEqual(len(ev), 1)
        self.assertEqual(ev[0]["sha"], ct.ticket_sha(d))

    def test_verify_after_the_ticket_moved_on_still_uses_the_signed_hash(self):
        """Regression (found by the golden run): the orchestrator overwrites the row sha with a change-set hash at DONE."""
        d = fresh()
        ct.register_contract(self.board, d)
        tid, ep = d["ticket_meta"]["id"], d["ticket_meta"]["epoch"]
        for state in ("SEALED", "LEASED", "IN_PROGRESS", "VERIFYING"):
            self.board.set_ticket(tid, state, owner="ORCHESTRATOR", epoch=ep)
        self.board.set_ticket(tid, "DONE", owner="ORCHESTRATOR", epoch=ep, sha="fa3b483cc3513ce6", note="done")
        self.assertEqual(ct.verify_against_board(self.board, d), (True, ""))
        forged = json.loads(json.dumps(d))
        forged["ticket_meta"]["touches"] = list(forged["ticket_meta"]["touches"]) + ["src/evil.py"]
        ok, why = ct.verify_against_board(self.board, forged)
        self.assertFalse(ok)
        self.assertIn("differs", why)

    def test_register_an_hmac_signed_contract_needs_the_key(self):
        """Regression (found by the golden run): the orchestrator registers tickets with no explicit key, so the key must come from the environment."""
        d = fresh(signed=False)
        ct.sign(d, key=b"k" * 32)
        with self.assertRaises(ct.ContractError):
            ct.register_contract(self.board, d, key=b"w" * 32)
        saved = os.environ.get(ct.DEFAULT_KEY_ENV)
        try:
            os.environ.pop(ct.DEFAULT_KEY_ENV, None)
            with self.assertRaises(ct.ContractError):
                ct.register_contract(self.board, d)
            os.environ[ct.DEFAULT_KEY_ENV] = "k" * 32
            self.assertEqual(ct.register_contract(self.board, d)["state"], "CONTRACTED")
        finally:
            if saved is None:
                os.environ.pop(ct.DEFAULT_KEY_ENV, None)
            else:
                os.environ[ct.DEFAULT_KEY_ENV] = saved
        self.assertEqual(ct.register_contract(self.board, d, key=b"k" * 32)["state"], "CONTRACTED")

    def test_tampering_after_signing_is_caught_even_if_resigned(self):
        d = fresh()
        ct.register_contract(self.board, d)
        evil = copy.deepcopy(d)
        evil["worker_scopes"]["worker_qa_guard"]["mutation_gate"]["min_kill_score"] = 0.1
        ct.sign(evil)  # a worker recomputes the plain sha256 - still valid on its own
        self.assertEqual(ct.validate(evil).errors, [])
        ok, why = ct.verify_against_board(self.board, evil)
        self.assertFalse(ok)
        self.assertIn("differs from the contract AG1 signed", why)

    def test_unregistered_and_planned_tickets_do_not_verify(self):
        d = fresh()
        self.assertEqual(ct.verify_against_board(self.board, d)[0], False)
        self.board.set_ticket(d["ticket_meta"]["id"], "PLANNED", epoch=14)
        ok, why = ct.verify_against_board(self.board, d)
        self.assertFalse(ok)
        self.assertIn("no signed contract", why)

    def test_epoch_mismatch(self):
        d = fresh()
        ct.register_contract(self.board, d)
        d2 = copy.deepcopy(d)
        d2["ticket_meta"]["epoch"] = 15
        ct.sign(d2)
        self.board.set_ticket(d["ticket_meta"]["id"], "CONTRACTED", sha=ct.ticket_sha(d2))
        ok, why = ct.verify_against_board(self.board, d2)
        self.assertFalse(ok)
        self.assertIn("epoch mismatch", why)

    def test_invalid_or_unsigned_cannot_be_registered(self):
        with self.assertRaises(ct.ContractError):
            ct.register_contract(self.board, fresh(signed=False))
        d = fresh()
        d["ticket_meta"]["epoch"] = 0
        with self.assertRaises(ct.ContractError):
            ct.register_contract(self.board, d)

    def test_contract_cannot_be_replaced_after_sealing(self):
        d = fresh()
        tid = d["ticket_meta"]["id"]
        ct.register_contract(self.board, d)
        self.board.set_ticket(tid, "SEALED")
        with self.assertRaises(ct.ContractError):
            ct.register_contract(self.board, d)

    def test_reregistration_while_contracted_updates_the_hash(self):
        d = fresh()
        ct.register_contract(self.board, d)
        d2 = copy.deepcopy(d)
        d2["ticket_meta"]["goal"] = "changed before sealing"
        ct.sign(d2)
        ct.register_contract(self.board, d2)
        self.assertEqual(ct.verify_against_board(self.board, d2), (True, ""))
        self.assertFalse(ct.verify_against_board(self.board, d)[0])

    def test_ticket_sha_ignores_the_signature_field(self):
        d = fresh()
        sha = ct.ticket_sha(d)
        ct.sign(d, key=b"zzz")
        self.assertEqual(ct.ticket_sha(d), sha)


if __name__ == "__main__":
    unittest.main()
