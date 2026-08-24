"""Path traversal through a data-source identifier.

CodeQL flagged ``py/path-injection`` on ``app/api/v2/deps.py``: a ``db_flag`` arrives from a URL and
was interpolated straight into ``database_schemas/<db_flag>/...``, so ``../../..`` walked out of the
directory. Five other modules built the same path the same way.

The identifier is *rejected*, never sanitised — silently rewriting ``../../etc`` to ``etc`` turns an
attack into a confusing answer about a data source the caller never named.
"""

from __future__ import annotations

import pytest

from app.platform import paths
from app.platform.paths import UnsafeSourceId

TRAVERSAL = [
    pytest.param("../../etc/passwd", id="posix-traversal"),
    pytest.param("..\\..\\windows\\system32", id="windows-traversal"),
    pytest.param("..", id="parent"),
    pytest.param(".", id="self"),
    pytest.param("demo/../../..", id="traversal-after-a-real-name"),
    pytest.param("a/b", id="separator"),
    pytest.param("a\\b", id="backslash-separator"),
    pytest.param("C:\\Windows", id="drive-letter"),
    pytest.param("\\\\server\\share", id="unc-path"),
    pytest.param("/etc/passwd", id="absolute"),
    pytest.param("demo\x00.txt", id="nul-byte"),
    pytest.param("demo%2f..%2f..", id="url-encoded"),
    pytest.param("café", id="non-ascii"),
    pytest.param("de mo", id="space"),
    pytest.param("", id="empty"),
    pytest.param("   ", id="whitespace"),
    pytest.param("x" * 65, id="too-long"),
]

BUILDERS = [
    paths.source_dir,
    paths.schema_dir,
    paths.schema_index_path,
    paths.intro_path,
]


class TestValidation:
    @pytest.mark.parametrize("value", TRAVERSAL)
    def test_a_dangerous_identifier_is_refused(self, value):
        with pytest.raises(UnsafeSourceId):
            paths.validate_source_id(value)

    @pytest.mark.parametrize("value", ["demo", "eval_store", "crm-db", "a", "x" * 64, "DB1"])
    def test_a_real_identifier_is_accepted(self, value):
        assert paths.validate_source_id(value) == value

    def test_surrounding_whitespace_is_trimmed_not_rejected(self):
        assert paths.validate_source_id("  demo  ") == "demo"

    def test_the_rejected_value_is_not_echoed_back(self):
        """The message reaches logs, and the value is attacker-controlled."""
        with pytest.raises(UnsafeSourceId) as exc_info:
            paths.validate_source_id("../../etc/passwd")
        assert "etc/passwd" not in str(exc_info.value)

    def test_the_non_raising_form_agrees(self):
        assert paths.is_safe_source_id("demo") is True
        assert paths.is_safe_source_id("../../etc") is False


class TestEveryBuilderIsGuarded:
    """One guard is only useful if every path builder goes through it."""

    @pytest.mark.parametrize("builder", BUILDERS, ids=lambda b: b.__name__)
    @pytest.mark.parametrize("value", TRAVERSAL)
    def test_no_builder_can_be_walked_out_of_the_schema_root(self, builder, value, tmp_path):
        with pytest.raises(UnsafeSourceId):
            builder(value, tmp_path)

    @pytest.mark.parametrize("builder", BUILDERS, ids=lambda b: b.__name__)
    def test_a_valid_identifier_stays_inside_the_root(self, builder, tmp_path):
        root = tmp_path
        result = builder("demo", root)
        assert paths.schemas_root(root).resolve() in result.resolve().parents

    def test_the_paths_are_the_ones_the_rest_of_the_code_expects(self, tmp_path):
        assert paths.schema_index_path("demo", tmp_path).name == "schema_index.yaml"
        assert paths.schema_dir("demo", tmp_path).name == "schema"
        assert paths.intro_path("demo", tmp_path).name == "demo_intro.txt"
        assert paths.source_dir("demo", tmp_path).name == "demo"


class TestTheCallSitesActuallyUseIt:
    """A validator nothing calls protects nothing — the same lesson as the CSRF sweep."""

    @pytest.mark.parametrize(
        "builder",
        [
            "app.api.v2.deps.schema_index_path",
            "app.sqlpolicy.scope_loader.schema_index_path",
        ],
    )
    def test_the_public_helpers_refuse_traversal(self, builder):
        import importlib

        module_name, _, attribute = builder.rpartition(".")
        function = getattr(importlib.import_module(module_name), attribute)
        with pytest.raises(UnsafeSourceId):
            function("../../etc/passwd")

    def test_no_module_still_builds_the_path_by_hand(self):
        """Catches a sixth call site being added the old way."""
        import pathlib

        offenders = []
        for path in pathlib.Path("app").rglob("*.py"):
            if path.name == "paths.py":
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            if '"database_schemas" /' in text or "'database_schemas' /" in text:
                offenders.append(str(path))
        assert offenders == [], (
            "these build a schema path by hand instead of going through app.platform.paths, "
            "which is how the traversal got in: " + ", ".join(offenders)
        )
