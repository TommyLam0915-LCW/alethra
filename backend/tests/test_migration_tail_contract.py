"""The migration head a deployment is named by must be an approved literal.

``start_e2e_research_flow.sh`` refuses to start or resume a stack whose schema it
cannot name, and it compares the head the live database reports against that
name. Both halves only mean something if the name is not produced by the same
lookup it is checked against — the previous revision read the head out of
alembic and then asked alembic the same question, so a migration added, deleted
or re-parented moved the expectation and the measurement together and nothing
was refused.

``scripts/check_migration_tail.py`` keeps three sources apart: a pinned tail
written by hand, a DAG parsed out of the revision modules with ``ast``, and
alembic's own resolver. This file exercises each refusal, including the case the
self-comparison could never catch — a tree that is internally consistent and
still not the one that was approved.
"""

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
CHECKER = BACKEND / "scripts" / "check_migration_tail.py"

_spec = importlib.util.spec_from_file_location("check_migration_tail", CHECKER)
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)


def _run(*args):
    return subprocess.run(
        [sys.executable, str(CHECKER), *args],
        capture_output=True, text=True, cwd=str(BACKEND),
    )


def _synth_tree(revisions, destination):
    """A migrations tree of bare ``revision = ...`` modules, nothing imported."""
    versions = destination / "migrations" / "versions"
    versions.mkdir(parents=True)
    for revision, down in revisions:
        (versions / f"{revision}.py").write_text(
            f"revision = {revision!r}\ndown_revision = {down!r}\n"
        )
    return destination


def _copied_real_tree(destination):
    """This checkout's ``migrations`` plus ``alembic.ini`` into an isolated dir.

    ``env.py`` is never executed here: ``ScriptDirectory`` only reads the
    revision modules, so the copy resolves without importing the application.
    """
    import shutil

    shutil.copytree(BACKEND / "migrations", destination / "migrations",
                    ignore=shutil.ignore_patterns("__pycache__", "*.db"))
    shutil.copy(BACKEND / "alembic.ini", destination / "alembic.ini")
    return destination


def _alembic_revision_count(backend_dir):
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    cfg = Config(os.path.join(backend_dir, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(backend_dir, "migrations"))
    return len(list(ScriptDirectory.from_config(cfg).walk_revisions()))


# (head -> root) chain used by the synthetic trees: the tail the pin approves,
# with the ancestors this checkout gives it in front of it — the oldest being a
# synthetic root, so a second child of that root is a branch. Spelled out rather
# than built from ``checker.EXPECTED_TAIL``: a chain derived from the pin would
# move with it, and these trees exist to show that the check refuses a claim
# about the tree instead of re-stating the tree.
_CHAIN = [
    ("a1b2c3d4e5f6", None),
    ("e1f2a3b4c5d6", "a1b2c3d4e5f6"),
    ("f2a3b4c5d6e7", "e1f2a3b4c5d6"),
    ("b2c3d4e5f6g7", "f2a3b4c5d6e7"),
    ("c3d4e5f6g7h8", "b2c3d4e5f6g7"),
    ("d4e5f6g7h8i9", "c3d4e5f6g7h8"),
    ("e5f6g7h8i9j0", "d4e5f6g7h8i9"),
]


class TestPinnedTail:
    def test_this_checkout_ends_where_the_pin_says(self):
        proc = _run("--with-alembic")
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == checker.EXPECTED_TAIL[-1]

    def test_the_pin_is_a_literal_not_a_restatement_of_the_tree(self):
        """Reading it back out of the DAG would collapse the sources again.

        The declaration has to be spelled-out identifiers on the page, so a
        rewrite of the revision modules cannot move the expectation along with
        the measurement.
        """
        declaration = CHECKER.read_text(encoding="utf-8").split("EXPECTED_TAIL = (", 1)[1]
        declaration = declaration.split(")", 1)[0]
        listed = [line.strip().strip(",").strip('"') for line in declaration.splitlines() if line.strip()]

        assert listed == list(checker.EXPECTED_TAIL), declaration
        for derived_from_the_tree in ("get_heads", "ScriptDirectory", "parse_tree"):
            assert derived_from_the_tree not in declaration

    def test_the_two_resolvers_name_the_same_head_and_count(self):
        """The independent parse and alembic, on the real tree, agreeing."""
        tree = checker.parse_tree(str(BACKEND))

        assert checker.heads_of(tree) == [checker.alembic_head(str(BACKEND))]
        assert len(tree) == _alembic_revision_count(str(BACKEND))


class TestRefusals:
    def test_a_second_head_is_refused_by_name(self, tmp_path):
        _synth_tree(_CHAIN + [("ff00ff00ff00", "a1b2c3d4e5f6")], tmp_path)

        proc = _run("--backend-dir", str(tmp_path))
        assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
        for name in ("ff00ff00ff00", "e5f6g7h8i9j0"):
            assert name in proc.stderr, proc.stderr

    def test_a_migration_added_on_top_is_refused_although_the_tree_is_valid(self, tmp_path):
        """Both resolvers agree, and the pin still says no.

        This is the case a derived expectation cannot see: the chain is coherent,
        single-headed, and alembic upgrades to it without complaint — it simply
        is not the schema this deployment was approved against.
        """
        copied = _copied_real_tree(tmp_path)
        head = checker.EXPECTED_TAIL[-1]
        (copied / "migrations" / "versions" / "added_on_top.py").write_text(
            f"revision = 'added_on_top'\ndown_revision = {head!r}\n"
        )

        assert checker.heads_of(checker.parse_tree(str(copied))) == ["added_on_top"]
        assert checker.alembic_head(str(copied)) == "added_on_top"

        proc = _run("--backend-dir", str(copied), "--with-alembic")
        assert proc.returncode == 3, (proc.returncode, proc.stdout, proc.stderr)
        assert "TAIL_MISMATCH" in proc.stderr, proc.stderr

    def test_a_dropped_tail_revision_leaves_two_heads_not_a_shorter_tail(self, tmp_path):
        """The hole shows up as a branch, because the orphan is a head too.

        Deleting ``d0e1f2a3b4c5`` does not shorten the chain for the resolver
        that walks backwards from a single head — it leaves ``c9d0e1f2a3b4``
        with no child, so there is no single head to name and the entry point
        refuses before it can compare anything with the pin.
        """
        copied = _copied_real_tree(tmp_path)
        versions = copied / "migrations" / "versions"
        for path in sorted(versions.glob("*.py")):
            if path.name.startswith("d0e1f2a3b4c5"):
                path.unlink()
                break
        else:
            raise AssertionError("the pinned tail is not in this checkout's modules")

        proc = _run("--backend-dir", str(copied))
        assert proc.returncode == 2, (proc.returncode, proc.stderr)
        assert "BRANCHED_TREE" in proc.stderr, proc.stderr
        for orphan in ("c9d0e1f2a3b4", "e5f6g7h8i9j0"):
            assert orphan in proc.stderr, proc.stderr

    def test_an_unparseable_revision_module_is_named_not_skipped(self, tmp_path):
        _synth_tree(_CHAIN, tmp_path)
        (tmp_path / "migrations" / "versions" / "broken.py").write_text(
            "this is not a module <<<\n"
        )

        proc = _run("--backend-dir", str(tmp_path))
        assert proc.returncode == 3, (proc.returncode, proc.stderr)
        assert "UNPARSEABLE" in proc.stderr, proc.stderr
        assert "broken.py" in proc.stderr, proc.stderr

    def test_a_healthy_synthetic_tree_is_accepted(self, tmp_path):
        """The refusals above are the check working, not the check being broken."""
        _synth_tree(_CHAIN, tmp_path)

        proc = _run("--backend-dir", str(tmp_path))
        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == "e5f6g7h8i9j0"


class TestTreeCoherence:
    """The tree must describe one upgrade path before any pin is consulted.

    A tail that matches the approval proves nothing if the graph underneath it
    has two modules claiming one revision, a parent nothing defines, or a node
    the head cannot walk back to — ``alembic upgrade head`` would then be
    bringing up a schema the checker described differently.
    """

    def test_this_checkout_names_each_revision_once(self):
        tree = checker.parse_tree(str(BACKEND))
        sources = checker.revision_sources(str(BACKEND))

        assert sorted(sources) == sorted(tree)
        claimed_twice = {r: p for r, p in sources.items() if len(p) > 1}
        assert claimed_twice == {}, claimed_twice
        # Collapsing is only visible by comparing a per-file count with a per-key
        # one; either alone can look internally consistent.
        assert sum(len(p) for p in sources.values()) == len(tree)

    def test_every_parent_this_checkout_names_is_defined_here(self):
        assert checker.missing_dependencies(checker.parse_tree(str(BACKEND))) == {}

    def test_every_revision_is_an_ancestor_of_the_one_head(self):
        """Both resolvers, same answer: nothing is stranded off the upgrade path."""
        tree = checker.parse_tree(str(BACKEND))
        heads = checker.heads_of(tree)

        assert len(heads) == 1, heads
        assert checker.unreachable_from(tree, heads) == []

        from alembic.config import Config
        from alembic.script import ScriptDirectory

        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND / "migrations"))
        script = ScriptDirectory.from_config(cfg)
        assert script.get_heads() == heads
        assert {r.revision for r in script.walk_revisions()} == set(tree)

    def test_a_second_module_claiming_one_revision_is_refused_by_name(self, tmp_path):
        tree = _synth_tree(_CHAIN, tmp_path)
        (tree / "migrations" / "versions" / "second_claim_of_head.py").write_text(
            "revision = 'e5f6g7h8i9j0'\ndown_revision = 'd4e5f6g7h8i9'\n"
        )

        proc = _run("--backend-dir", str(tmp_path))
        assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
        assert "DUPLICATE_REVISION" in proc.stderr, proc.stderr
        assert "second_claim_of_head.py" in proc.stderr, proc.stderr

    def test_a_parent_no_module_defines_is_refused_although_there_is_one_head(self, tmp_path):
        """One head and no tail mismatch is not the same as a walkable tree."""
        _synth_tree([("ghostly", "a_parent_that_was_never_written")], tmp_path)

        proc = _run("--backend-dir", str(tmp_path))
        assert proc.returncode == 2, (proc.returncode, proc.stdout, proc.stderr)
        assert "MISSING_DEPENDENCY" in proc.stderr, proc.stderr
        for name in ("ghostly", "a_parent_that_was_never_written"):
            assert name in proc.stderr, proc.stderr


class TestEntryPoints:
    def test_show_head_names_the_pinned_head_and_writes_nothing(self, tmp_path):
        """``--show-head`` is the read-only entry, so its refusal must be too."""
        env = dict(
            os.environ,
            E2E_PYTHON=sys.executable,
            E2E_ENV_FILE=str(tmp_path / "credentials.env"),
            E2E_LOG_DIR=str(tmp_path / "logs"),
        )
        proc = subprocess.run(
            ["bash", str(REPO / "start_e2e_research_flow.sh"), "--show-head"],
            capture_output=True, text=True, env=env, cwd=str(REPO),
        )

        assert proc.returncode == 0, proc.stderr
        assert proc.stdout.strip() == f"EXPECTED_ALEMBIC_HEAD={checker.EXPECTED_TAIL[-1]}"
        assert not (tmp_path / "credentials.env").exists()
        assert not (tmp_path / "logs").exists()

    def test_show_head_refuses_without_printing_a_head(self, tmp_path):
        """A python that cannot read the tree aborts; it does not name nothing."""
        proc = subprocess.run(
            ["bash", str(REPO / "start_e2e_research_flow.sh"), "--show-head"],
            capture_output=True, text=True,
            env=dict(os.environ, E2E_PYTHON=str(tmp_path / "no-such-python"),
                     E2E_ENV_FILE=str(tmp_path / "credentials.env"),
                     E2E_LOG_DIR=str(tmp_path / "logs")),
            cwd=str(REPO),
        )

        assert proc.returncode == 1, (proc.returncode, proc.stdout, proc.stderr)
        assert proc.stdout.strip() == "", "a refusal may not print a head"
        assert "not executable" in proc.stderr, proc.stderr
        assert "ABORTED" in proc.stderr, proc.stderr
        assert not (tmp_path / "logs").exists()
