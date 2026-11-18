import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import work


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        try:
            code = work.main(list(argv))
        except SystemExit as exc:
            code = exc.code
    text = out.getvalue().strip()
    return code, json.loads(text) if text else None


def make_project(root, name="demo", allow="example.com", budget="10"):
    project = root / name
    project.mkdir(parents=True, exist_ok=True)
    (project / "inbox").mkdir(exist_ok=True)
    code, obj = run([
        "init", str(project), "--goal", "цель", "--owner", "роль",
        "--allow", allow, "--budget-usd", budget,
    ])
    assert code == 0, obj
    return project


def add_file(project, name, content="содержимое", subdir="inbox"):
    path = project / subdir / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def add_source(project, sid="S1", filename="note.md", content="текст",
               kind="own", key=None, url=None, locator="раздел"):
    add_file(project, filename, content)
    argv = [
        "source", str(project), "--id", sid,
        "--file", f"inbox/{filename}", "--kind", kind,
        "--locator", locator, "--key", key or sid.lower(),
    ]
    if url:
        argv += ["--url", url]
    return run(argv)


def add_record(project, kind, obj, key):
    return run([
        "record", str(project), "--kind", kind,
        "--json", json.dumps(obj, ensure_ascii=False), "--key", key,
    ])


def fact(project, cid="C1", refs=None, locator="место", **extra):
    obj = {
        "id": cid, "type": "fact", "text": f"текст {cid}",
        "refs": refs if refs is not None else ["S1"],
    }
    if locator is not None:
        obj["locator"] = locator
    obj.update(extra)
    return add_record(project, "claim", obj, cid.lower())


def option(project, oid, approach, refs=None, objections=None, key=None):
    obj = {
        "id": oid, "approach": approach, "text": f"вариант {oid}",
        "refs": refs if refs is not None else ["C1"],
        "objections": objections if objections is not None else ["возражение"],
    }
    return add_record(project, "option", obj, key or oid.lower())


def decision(project, did="D1", approval="example", refs=None, **extra):
    obj = {
        "id": did, "text": f"решение {did}",
        "refs": refs if refs is not None else ["C1", "O1"],
        "option": "O1", "owner": "роль", "due": "2026-12-18",
        "metric": "метрика", "trigger": "условие", "approval": approval,
    }
    obj.update(extra)
    return add_record(project, "decision", obj, did.lower())


def state_of(project):
    return json.loads((project / "state.json").read_text(encoding="utf-8"))


def seed_chain(project):
    """S1 -> C1(fact) -> O1,O2,O3 -> D1(example)."""
    assert add_source(project)[0] == 0
    assert fact(project)[0] == 0
    for i, approach in enumerate(("status_quo", "close", "growth"), 1):
        assert option(project, f"O{i}", approach)[0] == 0
    assert decision(project)[0] == 0
    code, obj = run([
        "checkpoint", str(project), "--stage", "followup",
        "--next", "сопровождение", "--key", "seed-fu",
    ])
    assert code == 0, obj


class InitTests(unittest.TestCase):
    def test_init_state_and_dirs(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "demo"
            (project / "inbox").mkdir(parents=True)
            (project / "brief.md").write_text("бриф", encoding="utf-8")
            code, obj = run([
                "init", str(project), "--goal", "вопрос", "--owner",
                "владелец", "--allow", "Example.COM", "--allow", "example.com",
                "--budget-usd", "2.5",
            ])
            self.assertEqual(code, 0, obj)
            state = state_of(project)
            self.assertEqual(state["version"], 1)
            self.assertEqual(state["stage"], "brief")
            self.assertEqual(state["allow_hosts"], ["example.com"])
            self.assertEqual(state["budget_usd"], "2.5")
            self.assertTrue((project / "materials").is_dir())
            code, obj = run(["status", str(project)])
            self.assertEqual(code, 0)
            self.assertEqual(obj["counts"]["sources"], 0)

    def test_init_refuses_existing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp))
            code, obj = run([
                "init", str(project), "--goal", "x", "--owner", "y",
            ])
            self.assertEqual(code, 1)
            self.assertIn("уже существует", obj["error"])
            (project / "state.json").write_text("не json", encoding="utf-8")
            code, obj = run([
                "init", str(project), "--goal", "x", "--owner", "y",
            ])
            self.assertEqual(code, 1)

    def test_init_rejects_bad_inputs(self):
        with tempfile.TemporaryDirectory() as tmp:
            for index, extra in enumerate((
                ["--allow", "localhost"],
                ["--allow", "127.0.0.1"],
                ["--allow", "no-dot"],
                ["--allow", "https://example.com"],
                ["--budget-usd", "abc"],
                ["--budget-usd", "-1"],
                ["--budget-usd", "NaN"],
            )):
                with self.subTest(extra=extra):
                    project = Path(tmp) / f"bad{index}"
                    code, obj = run([
                        "init", str(project), "--goal", "g", "--owner", "o",
                        *extra,
                    ])
                    self.assertEqual(code, 1, obj)


class MutationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.project = make_project(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_key_required_and_idempotency(self):
        add_file(self.project, "a.md")
        code, _ = run([
            "record", str(self.project), "--kind", "claim",
            "--json", '{"id":"C1","type":"hypothesis","text":"x","refs":[]}',
        ])
        self.assertEqual(code, 2)
        payload = {"id": "C1", "type": "hypothesis", "text": "x", "refs": []}
        code, obj = add_record(self.project, "claim", payload, "k1")
        self.assertEqual(code, 0, obj)
        rev = obj["revision"]
        code, obj = add_record(self.project, "claim", payload, "k1")
        self.assertEqual(code, 0)
        self.assertTrue(obj["idempotent"])
        self.assertEqual(obj["revision"], rev)
        other = dict(payload, text="другой текст")
        code, obj = add_record(self.project, "claim", other, "k1")
        self.assertEqual(code, 1)
        self.assertIn("ключ", obj["error"].lower())
        self.assertEqual(state_of(self.project)["revision"], rev)

    def test_invalid_refs_and_fact_rules(self):
        code, obj = add_record(self.project, "claim", {
            "id": "C1", "type": "fact", "text": "x", "refs": ["NOPE"],
            "locator": "л",
        }, "c1")
        self.assertEqual(code, 1)
        self.assertIn("не найден", obj["error"])
        for payload in (
            {"id": "C1", "type": "fact", "text": "x", "refs": [], "locator": "л"},
            {"id": "C1", "type": "fact", "text": "x", "refs": ["C1"], "locator": "л"},
            {"id": "C1", "type": "fact", "text": "x", "refs": ["S1"]},
            {"id": "C1", "type": "weird", "text": "x", "refs": []},
            {"id": "bad id!", "type": "unknown", "text": "x", "refs": []},
        ):
            with self.subTest(payload=payload):
                code, obj = add_record(self.project, "claim", payload, "kx")
                self.assertEqual(code, 1, obj)

    def test_fact_rejects_synthetic_only_ancestry(self):
        add_source(self.project, "SS", "syn.md", kind="synthetic")
        code, obj = add_record(self.project, "claim", {
            "id": "H1", "type": "hypothesis", "text": "h", "refs": ["SS"],
        }, "h1")
        self.assertEqual(code, 0, obj)
        code, obj = add_record(self.project, "claim", {
            "id": "C1", "type": "fact", "text": "f", "refs": ["H1"],
            "locator": "л",
        }, "c1")
        self.assertEqual(code, 1)
        self.assertIn("синтетические", obj["error"])
        add_source(self.project, "S1", "own.md", kind="own")
        code, obj = add_record(self.project, "claim", {
            "id": "C2", "type": "fact", "text": "f", "refs": ["H1", "S1"],
            "locator": "л",
        }, "c2")
        self.assertEqual(code, 0, obj)

    def test_transitive_retraction_and_impact(self):
        seed_chain(self.project)
        code, obj = run([
            "retract", str(self.project), "S1",
            "--reason", "ошибка в источнике", "--key", "r1",
        ])
        self.assertEqual(code, 0, obj)
        state = state_of(self.project)
        self.assertEqual(state["sources"]["S1"]["status"], "retracted")
        self.assertEqual(state["claims"]["C1"]["status"], "stale")
        self.assertTrue(all(
            state["options"][f"O{i}"]["status"] == "stale" for i in (1, 2, 3)
        ))
        self.assertEqual(state["decisions"]["D1"]["status"], "review_required")
        self.assertEqual(state["stage"], "followup")
        code, obj = run(["impact", str(self.project), "S1"])
        self.assertEqual(code, 0)
        ids = {d["id"] for d in obj["dependents"]}
        self.assertEqual(ids, {"C1", "O1", "O2", "O3", "D1"})
        c1 = next(d for d in obj["dependents"] if d["id"] == "C1")
        self.assertEqual(c1["paths"], [["S1", "C1"]])
        code, obj = run(["check", str(self.project)])
        self.assertEqual(code, 0, obj)
        self.assertTrue(obj["warnings"])
        self.assertFalse(obj["errors"])

    def test_fact_ignores_nonfact_ancestry(self):
        add_source(self.project)
        code, obj = fact(self.project, "C1", refs=["S1"])
        self.assertEqual(code, 0, obj)
        code, obj = add_record(self.project, "claim", {
            "id": "H1", "type": "hypothesis", "text": "h",
            "refs": ["S1"],
        }, "h1")
        self.assertEqual(code, 0, obj)
        code, obj = fact(self.project, "F1", refs=["H1"])
        self.assertEqual(code, 1)
        self.assertIn("синтетические", obj["error"])
        code, obj = fact(self.project, "F2", refs=["C1"])
        self.assertEqual(code, 0, obj)
        state = state_of(self.project)
        self.assertEqual(state["claims"]["F2"]["status"], "active")

    def test_overbudget_blocks_checkpoint_not_cost(self):
        code, obj = run([
            "cost", str(self.project), "--kind", "tool",
            "--usd", "11", "--note", "дорого", "--key", "k1",
        ])
        self.assertEqual(code, 0, obj)
        code, obj = run([
            "checkpoint", str(self.project), "--stage", "research",
            "--next", "x", "--key", "cp1",
        ])
        self.assertEqual(code, 1)
        self.assertIn("бюджет", obj["error"])
        code, obj = run([
            "cost", str(self.project), "--kind", "tool",
            "--usd", "0", "--minutes", "1", "--note", "учёт",
            "--key", "k2",
        ])
        self.assertEqual(code, 0, obj)
        self.assertEqual(state_of(self.project)["stage"], "brief")

    def test_impact_returns_one_shortest_path(self):
        add_source(self.project)
        for name in ("A0", "B0"):
            code, obj = add_record(self.project, "claim", {
                "id": name, "type": "unknown", "text": name,
                "refs": ["S1"],
            }, name.lower())
            self.assertEqual(code, 0, obj)
        for i in range(1, 20):
            for name in (f"A{i}", f"B{i}"):
                code, obj = add_record(self.project, "claim", {
                    "id": name, "type": "unknown", "text": name,
                    "refs": [f"A{i - 1}", f"B{i - 1}"],
                }, name.lower())
                self.assertEqual(code, 0, obj)
        code, obj = run(["impact", str(self.project), "S1"])
        self.assertEqual(code, 0, obj)
        deps = obj["dependents"]
        self.assertEqual(len(deps), 40)
        self.assertEqual(len({d["id"] for d in deps}), 40)
        by_id = {d["id"]: d for d in deps}
        for dep in deps:
            self.assertEqual(len(dep["paths"]), 1)
            path = dep["paths"][0]
            self.assertEqual(path[0], "S1")
            self.assertEqual(path[-1], dep["id"])
        self.assertEqual(len(by_id["A19"]["paths"][0]), 21)
        self.assertEqual(len(by_id["B0"]["paths"][0]), 2)

    def test_supersedes_replaces_and_invalidates(self):
        seed_chain(self.project)
        add_source(self.project, "S2", "two.md", content="v2")
        code, obj = add_record(self.project, "claim", {
            "id": "C9", "type": "fact", "text": "новый", "refs": ["C1"],
            "locator": "л", "supersedes": "C1",
        }, "c9")
        self.assertEqual(code, 1)
        self.assertIn("зависимые", obj["error"])
        code, obj = add_record(self.project, "claim", {
            "id": "C9", "type": "hypothesis", "text": "новый",
            "refs": ["S2"], "supersedes": "C1",
        }, "c9")
        self.assertEqual(code, 0, obj)
        state = state_of(self.project)
        self.assertEqual(state["claims"]["C1"]["status"], "superseded")
        self.assertEqual(state["claims"]["C9"]["status"], "active")
        self.assertEqual(state["options"]["O1"]["status"], "stale")
        self.assertEqual(state["decisions"]["D1"]["status"], "review_required")
        code, obj = add_record(self.project, "claim", {
            "id": "C10", "type": "hypothesis", "text": "x", "refs": ["C1"],
        }, "c10")
        self.assertEqual(code, 1)
        code, obj = add_record(self.project, "claim", {
            "id": "C10", "type": "hypothesis", "text": "x",
            "refs": [], "supersedes": "C1",
        }, "c10")
        self.assertEqual(code, 1)

    def test_option_gates(self):
        add_source(self.project)
        assert fact(self.project)[0] == 0
        code, obj = run([
            "checkpoint", str(self.project), "--stage", "options",
            "--next", "x", "--key", "g1",
        ])
        self.assertEqual(code, 0, obj)
        code, obj = run([
            "checkpoint", str(self.project), "--stage", "strategy",
            "--next", "x", "--key", "g2",
        ])
        self.assertEqual(code, 1)
        assert option(self.project, "O1", "status_quo")[0] == 0
        assert option(self.project, "O2", "close")[0] == 0
        code, obj = run([
            "checkpoint", str(self.project), "--stage", "strategy",
            "--next", "x", "--key", "g3",
        ])
        self.assertEqual(code, 1)
        assert option(self.project, "O3", "status_quo")[0] == 0
        code, obj = run([
            "checkpoint", str(self.project), "--stage", "strategy",
            "--next", "x", "--key", "g4",
        ])
        self.assertEqual(code, 1)
        assert option(self.project, "O4", "partner")[0] == 0
        code, obj = run([
            "checkpoint", str(self.project), "--stage", "strategy",
            "--next", "x", "--key", "g5",
        ])
        self.assertEqual(code, 0, obj)
        code, obj = run([
            "checkpoint", str(self.project), "--stage", "followup",
            "--next", "x", "--key", "g6",
        ])
        self.assertEqual(code, 1)
        assert decision(self.project, refs=["C1", "O1"])[0] == 0
        code, obj = run([
            "checkpoint", str(self.project), "--stage", "followup",
            "--next", "x", "--key", "g7",
        ])
        self.assertEqual(code, 0, obj)

    def test_followup_rejects_pending_accepts_example(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = make_project(Path(tmp), name="real")
            add_source(project)
            assert fact(project)[0] == 0
            for i, app in enumerate(("status_quo", "close", "x"), 1):
                assert option(project, f"O{i}", app)[0] == 0
            code, obj = decision(project, approval="pending")
            self.assertEqual(code, 0, obj)
            code, obj = run([
                "checkpoint", str(project), "--stage", "followup",
                "--next", "x", "--key", "f1",
            ])
            self.assertEqual(code, 1)
            code, obj = decision(
                project, "D2", approval="example", refs=["C1", "O1"],
            )
            self.assertEqual(code, 0, obj)
            code, obj = run([
                "checkpoint", str(project), "--stage", "followup",
                "--next", "x", "--key", "f2",
            ])
            self.assertEqual(code, 0, obj)

    def test_approved_requires_metadata_and_clean_deps(self):
        seed_chain(self.project)
        code, obj = decision(
            self.project, "D9", approval="approved",
            refs=["C1", "O1"], option="O1",
        )
        self.assertEqual(code, 1)
        self.assertIn("approved_by", obj["error"])
        code, obj = decision(
            self.project, "D9", approval="approved",
            refs=["C1", "O1"], option="O1", approved_by="человек",
        )
        self.assertEqual(code, 1)
        self.assertIn("approval_ref", obj["error"])
        code, obj = decision(
            self.project, "D9", approval="approved",
            refs=["C1", "O1"], option="O1", approved_by="человек",
            approval_ref="заметка о согласии",
        )
        self.assertEqual(code, 0, obj)
        run(["retract", str(self.project), "S1", "--reason", "x", "--key", "rr"])
        add_source(self.project, "S2", "two.md", content="v2")
        code, obj = add_record(self.project, "option", {
            "id": "O9", "approach": "new", "text": "новый",
            "refs": ["S2"], "objections": ["о"],
        }, "o9")
        self.assertEqual(code, 0, obj)
        code, obj = decision(
            self.project, "D10", approval="approved",
            refs=["D1", "O9"], option="O9", approved_by="человек",
            approval_ref="заметка",
        )
        self.assertEqual(code, 1)
        self.assertIn("active", obj["error"])

    def test_pause_resume_and_cost_allowed(self):
        seed_chain(self.project)
        code, obj = run([
            "pause", str(self.project), "--next", "стоп", "--key", "p1",
        ])
        self.assertEqual(code, 0, obj)
        code, obj = add_record(self.project, "claim", {
            "id": "C2", "type": "unknown", "text": "x", "refs": [],
        }, "c2")
        self.assertEqual(code, 1)
        self.assertIn("паузе", obj["error"])
        code, obj = run([
            "cost", str(self.project), "--kind", "model",
            "--note", "цена скрыта", "--key", "m1",
        ])
        self.assertEqual(code, 0, obj)
        code, obj = run(["status", str(self.project)])
        self.assertEqual(code, 0)
        self.assertTrue(obj["paused"])
        code, obj = run(["resume", str(self.project), "--key", "rs1"])
        self.assertEqual(code, 0, obj)
        self.assertEqual(obj["stage"], "followup")
        self.assertEqual(obj["next"], "стоп")
        code, obj = add_record(self.project, "claim", {
            "id": "C2", "type": "unknown", "text": "x", "refs": [],
        }, "c2")
        self.assertEqual(code, 0, obj)
        code, obj = run(["resume", str(self.project), "--key", "rs2"])
        self.assertEqual(code, 1)

    def test_cost_unknown_not_zero_and_budget(self):
        code, obj = run([
            "cost", str(self.project), "--kind", "model",
            "--note", "цена скрыта", "--key", "c1",
        ])
        self.assertEqual(code, 0, obj)
        code, obj = run(["status", str(self.project)])
        self.assertEqual(code, 0)
        self.assertEqual(obj["summary"]["unknown_entries"], 1)
        self.assertIsNone(obj["summary"]["total_usd"])
        self.assertEqual(obj["summary"]["known_usd"], "0")
        run([
            "cost", str(self.project), "--kind", "tool",
            "--usd", "20", "--minutes", "5", "--note", "дорого",
            "--key", "c2",
        ])
        code, obj = run(["status", str(self.project)])
        self.assertEqual(code, 1)
        self.assertTrue(obj["budget"]["exceeded"])
        code, obj = run(["check", str(self.project)])
        self.assertEqual(code, 1)
        self.assertTrue(any("бюджет" in e.lower() or "budget" in e.lower()
                            for e in obj["errors"]))

    def test_invalid_cost_and_dates(self):
        for argv_tail in (
            ["--usd", "NaN"], ["--usd", "-1"], ["--usd", "Infinity"],
            ["--minutes", "abc"],
        ):
            with self.subTest(argv_tail=argv_tail):
                code, obj = run([
                    "cost", str(self.project), "--kind", "tool",
                    *argv_tail, "--note", "x", "--key", "bad",
                ])
                self.assertEqual(code, 1, obj)
        add_source(self.project)
        assert fact(self.project)[0] == 0
        for due in ("2026-13-40", "not-a-date", "18.12.2026"):
            with self.subTest(due=due):
                code, obj = add_record(self.project, "decision", {
                    "id": "D1", "text": "x", "refs": ["C1"],
                    "option": "C1", "owner": "о", "due": due,
                    "metric": "м", "trigger": "т", "approval": "pending",
                }, "d1")
                self.assertEqual(code, 1, obj)

    def test_option_field_rules(self):
        add_source(self.project)
        assert fact(self.project)[0] == 0
        code, obj = add_record(self.project, "option", {
            "id": "O1", "approach": "a", "text": "x", "refs": [],
            "objections": ["о"],
        }, "o1")
        self.assertEqual(code, 1)
        code, obj = add_record(self.project, "option", {
            "id": "O1", "approach": "a", "text": "x", "refs": ["C1"],
            "objections": [],
        }, "o1")
        self.assertEqual(code, 1)
        code, obj = add_record(self.project, "decision", {
            "id": "D1", "text": "x", "refs": ["C1"], "option": "O9",
            "owner": "о", "due": "2026-01-01", "metric": "м",
            "trigger": "т", "approval": "pending",
        }, "d1")
        self.assertEqual(code, 1)


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.project = make_project(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_input_symlink_and_traversal_escape(self):
        outside = self.root / "outside.txt"
        outside.write_text("секрет", encoding="utf-8")
        link = self.project / "inbox" / "evil.md"
        os.symlink(outside, link)
        code, obj = run([
            "source", str(self.project), "--id", "S1",
            "--file", "inbox/evil.md", "--kind", "own",
            "--locator", "л", "--key", "s1",
        ])
        self.assertEqual(code, 1)
        self.assertIn("пределы проекта", obj["error"])
        code, obj = run([
            "source", str(self.project), "--id", "S1",
            "--file", "../outside.txt", "--kind", "own",
            "--locator", "л", "--key", "s1",
        ])
        self.assertEqual(code, 1)
        add_file(self.project, "big.md", "x" * (5 * 1024 * 1024 + 1))
        code, obj = run([
            "source", str(self.project), "--id", "S1",
            "--file", "inbox/big.md", "--kind", "own",
            "--locator", "л", "--key", "s1",
        ])
        self.assertEqual(code, 1)
        self.assertIn("5 МиБ", obj["error"])

    def test_materials_and_state_symlink_escape(self):
        materials = self.project / "materials"
        real_materials = self.root / "real_materials"
        real_materials.mkdir()
        materials.rmdir()
        os.symlink(real_materials, materials)
        add_file(self.project, "a.md")
        code, obj = run([
            "source", str(self.project), "--id", "S1",
            "--file", "inbox/a.md", "--kind", "own",
            "--locator", "л", "--key", "s1",
        ])
        self.assertEqual(code, 1)
        self.assertIn("symlink", obj["error"])
        state = self.project / "state.json"
        moved = self.root / "state_moved.json"
        state.rename(moved)
        os.symlink(moved, state)
        code, obj = run(["status", str(self.project)])
        self.assertEqual(code, 1)
        self.assertIn("symlink", obj["error"])

    def test_tampered_state_fails_check(self):
        seed_chain(self.project)
        state = state_of(self.project)
        state["claims"]["C1"]["refs"] = ["GHOST"]
        (self.project / "state.json").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8",
        )
        code, obj = run(["check", str(self.project)])
        self.assertEqual(code, 1)
        self.assertTrue(obj["errors"])
        state = state_of(self.project)
        state["claims"]["C1"]["refs"] = ["S1"]
        (self.project / "state.json").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8",
        )
        material = self.project / "materials" / "S1.md"
        material.write_text("подменено", encoding="utf-8")
        code, obj = run(["check", str(self.project)])
        self.assertEqual(code, 1)
        self.assertTrue(any("хеш" in e for e in obj["errors"]))

    def test_tampered_empty_fact_refs_blocks_mutation(self):
        seed_chain(self.project)
        state = state_of(self.project)
        state["claims"]["C1"]["refs"] = []
        before = json.dumps(state, ensure_ascii=False).encode("utf-8")
        (self.project / "state.json").write_bytes(before)
        code, obj = run(["check", str(self.project)])
        self.assertEqual(code, 1)
        self.assertTrue(any("основания" in e for e in obj["errors"]))
        code, obj = add_record(self.project, "claim", {
            "id": "C9", "type": "unknown", "text": "x", "refs": [],
        }, "c9")
        self.assertEqual(code, 1)
        self.assertIn("Состояние не сохранено", obj["error"])
        self.assertEqual(
            (self.project / "state.json").read_bytes(), before,
        )

    def test_failed_replace_leaves_state_and_orphan_snapshot(self):
        add_source(self.project)
        before = (self.project / "state.json").read_bytes()
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            code, obj = add_record(self.project, "claim", {
                "id": "C1", "type": "unknown", "text": "x", "refs": [],
            }, "c1")
        self.assertEqual(code, 1)
        self.assertEqual((self.project / "state.json").read_bytes(), before)
        orphans = list(self.project.glob("state.json.tmp-*"))
        self.assertEqual(len(orphans), 1)
        snapshot = json.loads(orphans[0].read_text(encoding="utf-8"))
        self.assertIn("C1", snapshot["claims"])
        orphan_bytes = orphans[0].read_bytes()
        code, obj = add_record(self.project, "claim", {
            "id": "C2", "type": "unknown", "text": "y", "refs": [],
        }, "c2")
        self.assertEqual(code, 0, obj)
        self.assertTrue(orphans[0].exists())
        self.assertEqual(orphans[0].read_bytes(), orphan_bytes)
        self.assertNotEqual(
            (self.project / "state.json").read_bytes(), orphan_bytes,
        )

    def test_lock_conflict_blocks_mutators_only(self):
        lock = self.project / "state.json.lock"
        lock.write_text("999", encoding="utf-8")
        code, obj = add_record(self.project, "claim", {
            "id": "C1", "type": "unknown", "text": "x", "refs": [],
        }, "c1")
        self.assertEqual(code, 1)
        self.assertIn("lock", obj["error"])
        self.assertTrue(lock.exists())
        code, obj = run(["status", str(self.project)])
        self.assertEqual(code, 0)
        code, obj = run(["check", str(self.project)])
        self.assertEqual(code, 0)
        lock.unlink()
        code, obj = add_record(self.project, "claim", {
            "id": "C1", "type": "unknown", "text": "x", "refs": [],
        }, "c1")
        self.assertEqual(code, 0, obj)

    def test_open_url_rules(self):
        add_file(self.project, "a.md")
        for url in (
            "http://example.com/x",
            "https://user:pw@example.com/x",
            "https://example.com:8443/x",
            "https://example.com/x?token=abc",
            "https://127.0.0.1/x",
            "https://localhost/x",
        ):
            with self.subTest(url=url):
                code, obj = run([
                    "source", str(self.project), "--id", "S9",
                    "--file", "inbox/a.md", "--kind", "open",
                    "--url", url, "--locator", "л", "--key", "s9",
                ])
                self.assertEqual(code, 1, obj)
        code, obj = run([
            "source", str(self.project), "--id", "S9",
            "--file", "inbox/a.md", "--kind", "open",
            "--url", "https://example.com/services/x/", "--locator", "л",
            "--key", "s9",
        ])
        self.assertEqual(code, 0, obj)
        self.assertEqual(
            state_of(self.project)["sources"]["S9"]["kind"], "open",
        )

    def test_duplicate_material_never_overwritten(self):
        add_source(self.project, "S1", "one.md", content="v1")
        material = self.project / "materials" / "S1.md"
        material.write_bytes(b"orphan-different")
        state = state_of(self.project)
        del state["sources"]["S1"]
        (self.project / "state.json").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8",
        )
        code, obj = run([
            "source", str(self.project), "--id", "S1",
            "--file", "inbox/one.md", "--kind", "own",
            "--locator", "другое", "--key", "s1b",
        ])
        self.assertEqual(code, 1)
        self.assertIn("другим содержимым", obj["error"])
        self.assertEqual(material.read_bytes(), b"orphan-different")
        material.write_bytes(b"v1")
        code, obj = run([
            "source", str(self.project), "--id", "S1",
            "--file", "inbox/one.md", "--kind", "own",
            "--locator", "другое", "--key", "s1b",
        ])
        self.assertEqual(code, 0, obj)
        self.assertEqual(material.read_bytes(), b"v1")


if __name__ == "__main__":
    unittest.main()
