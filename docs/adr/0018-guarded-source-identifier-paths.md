# ADR 0018 — One guarded builder turns a `db_flag` into a path, and rejects rather than sanitises

**Status:** Accepted — 2026-08-24, Phase 9/13.

## Context

A data-source identifier (`db_flag`) arrives from a URL path, a query string or a JSON body, and six
modules interpolated it straight into `database_schemas/<db_flag>/…`. `../../../etc` walks out of
the directory; on Windows so do `..\`, a drive letter and a UNC prefix. CodeQL reported seven
`py/path-injection` findings and three `py/log-injection` findings, the latter because the same
untrusted value — newlines included — reached the logs, where a newline can forge a log entry.

Six call sites building the same path by hand is not six bugs to fix. It is one missing function.

## Decision

`app/platform/paths.py` is the only way to turn an identifier into a path, and it applies two
independent checks because either alone has a known bypass:

1. **The identifier must look like an identifier.** `SOURCE_ID_PATTERN = [A-Za-z0-9_-]{1,64}`. This
   is an allowlist, so nothing dangerous has to be enumerated — separators, `..`, NUL bytes, drive
   letters, UNC prefixes and Unicode look-alikes simply fail to match.
2. **The resolved path must still be inside the root.** Symlink resolution and platform-specific
   normalisation happen *after* step 1, so containment is observable rather than argued.

**The value is rejected, never rewritten.** Silently turning `../../etc` into `etc` converts an
attack into a confusing 404 against a data source the caller never named. The rejected value is also
not echoed back into the error message, because that message reaches the logs.

**A test enforces the shape, not just the behaviour.**
`tests/test_paths.py::TestTheCallSitesActuallyUseIt::test_no_module_still_builds_the_path_by_hand`
fails if a seventh module starts constructing the path itself.

## Consequences

**What it buys.** The path-injection and log-injection findings are closed at the entry point rather
than in six places, and validating there means the value is already safe by the time it reaches a
log call. New code gets the guarantee by using the obvious helper.

**What it costs.**

* **A real constraint on identifiers.** No dots, spaces, or non-ASCII characters. A source named
  `sales.eu` cannot be enrolled or read and must be renamed — a migration burden for anyone who had
  such a name, imposed to remove a class of bug.
* **The guarantee covers only paths built through the builder.** Nothing in the type system prevents
  a future module from calling `Path(...)` itself; the sweep test detects the pattern it knows about,
  which is not the same as the pattern that will be written next.
* **A rejected identifier produces a deliberately unhelpful error.** Not naming the offending value
  is right for the log and mildly annoying for the operator who typo'd a name.
