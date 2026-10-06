#!/usr/bin/env bash
# check_docs_selftest.sh -- prove tests/check_docs.py catches faults: each check must PASS on a small good scratch tree and FAIL
# on the same tree with one fault. Prints one "ok|BAD <check>: ..." line per case; exit 1 if any case is wrong.
# Run: bash tests/lib/check_docs_selftest.sh (called by tests/run_tests.sh section == docs). PASS = every case prints "ok", exit 0.
# Docs: tests/TEST_MATRIX.md
cd "$(dirname "$0")/../.." || exit 1
CD="$PWD/tests/check_docs.py"; T="$(mktemp -d)"; trap 'rm -rf "$T"' EXIT; bad=0
mk() {   # fresh good tree in $T/t
  rm -rf "$T/t"; mkdir -p "$T/t/designs/x/output" "$T/t/designs/x/tb" "$T/t/scripts/docs" "$T/t/model/m" "$T/t/sub"
  printf 'ALL_DESIGNS := x\nMODELS := m\nfoo:\n\t@true\nhelp:\n\t@echo "  foo  all 1 designs"\n' > "$T/t/Makefile"
  printf 'ORDER = ["x"]\n' > "$T/t/scripts/docs/tables.py"; echo "m" > "$T/t/scripts/check_generated.sh"
  printf 'x\n' > "$T/t/designs/x/output/layout.png"; : > "$T/t/designs/x/output/flow.log"; : > "$T/t/designs/x/tb/x_tb.v"
  echo '{}' > "$T/t/designs/x/config.json"; echo '# n' > "$T/t/designs/x/NOTES.md"
  echo '{"design__instance__count__stdcell": 100, "design__instance__count__class:sequential_cell": 10, "design__die__bbox": "0.0 0.0 80.0 80.0", "timing__setup__ws__corner:nom_tt_025C_1v80": 5.123}' > "$T/t/designs/x/output/metrics.json"
  echo 'Status: hardened, 100 std cells, 10 flip-flops, 80 x 80 um die, setup slack 5.12 ns.' > "$T/t/designs/x/README.md"
  printf '# Twenty designs x\n\nSee [a](sub/a.md) and `make foo`.\n' | sed 's/Twenty/1/' > "$T/t/README.md"; cp "$T/t/README.md" "$T/t/CLAUDE.md"; echo ok > "$T/t/sub/a.md"
}
run() { (cd "$T/t" && CHECK_DOCS_ROOT="$T/t" python3 "$CD" "$1" >/dev/null 2>&1); }
expect() {   # expect <check> <PASS|FAIL> <label>
  run "$1"; rc=$?; want=0; [ "$2" = FAIL ] && want=1
  if [ $rc -eq $want ]; then echo "ok  $1: $3"; else echo "BAD $1: $3 (exit $rc)"; bad=1; fi
}
mk; for c in links targets inventory evidence; do expect $c PASS "good tree accepted"; done
mk; echo 'broken [x](sub/missing.md)' >> "$T/t/README.md"; expect links FAIL "broken relative link rejected"
mk; echo 'run `make nosuchtarget`' >> "$T/t/README.md"; expect targets FAIL "unknown make target rejected"
mk; rm "$T/t/designs/x/output/layout.png"; expect inventory FAIL "missing layout.png rejected"
mk; echo 'ORDER = []' > "$T/t/scripts/docs/tables.py"; expect inventory FAIL "design missing from tables.py ORDER rejected"
mk; sed -i.bak 's/100 std cells/101 std cells/' "$T/t/designs/x/README.md"; expect evidence FAIL "wrong std-cell count in Status rejected"
mk; sed -i.bak 's/5.12 ns/6.12 ns/' "$T/t/designs/x/README.md"; expect evidence FAIL "wrong setup slack in Status rejected"
exit $bad
