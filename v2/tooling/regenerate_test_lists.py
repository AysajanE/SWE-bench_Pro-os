#!/usr/bin/env python3
"""Regenerate grading lists from N reference and N empty-submission output.json files.

Only pytest node IDs containing :: are supported. Any shipped or measured name
without :: is an error, even if it would otherwise be filtered out.
Only tests whose file exactly matches selected_test_files_to_run are considered
(after normalizing relative POSIX paths). A test must pass in every reference run.
fail_to_pass contains those that never pass in an empty run; pass_to_pass contains
those that pass in every empty run. Changes between passed and not passed within
either group are unstable: exclude and report them. FAILED, ERROR and absence
all count as not passed, so changes among them do not make a test unstable.
Parametrized IDs containing the run's calendar year or next year in their
parameters are date-dependent: exclude and report them unless the exact ID is
already in either shipped grading list. --run-year defaults to the current year.
Repeat --exclude NODE_ID to omit exact IDs with other known instability, such as
test_published_in_future_year[1-True], whose now + 365 days stays in the same
year on January 1 of leap years. Explicit exclusions are reported in
excluded_tests. Shipped names absent from the regenerated lists are reported in
dropped_shipped_tests; any such drop exits nonzero without writing stdout.
Use the original shipped config as --config when regenerating additions.

Usage:
    python3 v2/tooling/regenerate_test_lists.py --config tests/config.json \
        --reference ref-1/output.json ref-2/output.json \
        --empty empty-1/output.json empty-2/output.json --run-year 2026 \
        > test-lists.json 2> excluded-tests.json
    python3 v2/tooling/regenerate_test_lists.py --self-test

The source config is read only. Stdout contains sorted fail_to_pass/pass_to_pass
fields, preserving each field's list or encoded-string type and encoding style.
Stderr contains sorted unstable_tests, date_dependent_tests, excluded_tests and
dropped_shipped_tests reports. Unstable statuses follow input-run order; null
means missing. No run execution or config/checksum writes occur.
"""

import argparse
import ast
import json
import posixpath
import sys
from datetime import date
from pathlib import Path


def load_run(path):
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("tests"), list):
        raise ValueError(f"{path}: expected an object with a tests list")
    results = {}
    for test in data["tests"]:
        if not isinstance(test, dict):
            raise ValueError(f"{path}: expected test objects")
        name, status = test.get("name"), test.get("status")
        if not isinstance(name, str) or not name or not isinstance(status, str) or not status:
            raise ValueError(f"{path}: each test needs a nonempty name and status")
        if name in results and results[name] != status:
            raise ValueError(f"{path}: conflicting statuses for {name}")
        results[name] = status
    return results


def decode_list(value, field):
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError:
            value = ast.literal_eval(value)
    if not isinstance(value, list) or not all(isinstance(p, str) and p for p in value):
        raise ValueError(f"{field} must be a list of nonempty strings")
    return value


def encode_list(tests, template):
    if not isinstance(template, str):
        return tests
    try:
        json.loads(template)
    except ValueError:
        return repr(tests)
    return json.dumps(tests)


def regenerate(reference_runs, empty_runs, selected_files, run_year=None, shipped_names=(),
               excluded_names=()):
    if not reference_runs or len(reference_runs) != len(empty_runs):
        raise ValueError("supply the same nonzero number of reference and empty runs")
    if run_year is None:
        run_year = date.today().year
    years = (str(run_year), str(run_year + 1))
    shipped = set(shipped_names)
    selected = {posixpath.normpath(name) for name in selected_files}
    names = set().union(*reference_runs, *empty_runs)
    unsupported = sorted(name for name in names | shipped if "::" not in name)
    if unsupported:
        raise ValueError("only pytest node IDs containing '::' are supported; "
                         "unsupported test names: " + json.dumps(unsupported))
    excluded = set(excluded_names)
    lists = {"fail_to_pass": [], "pass_to_pass": []}
    unstable = []
    date_dependent = []
    excluded_tests = []
    for name in sorted(names):
        if posixpath.normpath(name.split("::", 1)[0]) not in selected:
            continue
        if name in excluded:
            excluded_tests.append(name)
            continue
        reference = [run.get(name) for run in reference_runs]
        empty = [run.get(name) for run in empty_runs]
        parameters = name.split("::", 1)[-1].partition("[")[2]
        is_date_dependent = (parameters.endswith("]")
                             and any(year in parameters[:-1] for year in years)
                             and name not in shipped)
        if is_date_dependent:
            date_dependent.append(name)
        if (len({status == "PASSED" for status in reference}) > 1
                or len({status == "PASSED" for status in empty}) > 1):
            unstable.append({"name": name, "reference": reference, "empty": empty})
        elif not is_date_dependent and all(status == "PASSED" for status in reference):
            field = "pass_to_pass" if all(status == "PASSED" for status in empty) else "fail_to_pass"
            lists[field].append(name)
    dropped_shipped = sorted(shipped - set().union(*lists.values()))
    return lists, {"unstable_tests": unstable, "date_dependent_tests": date_dependent,
                   "excluded_tests": excluded_tests, "dropped_shipped_tests": dropped_shipped}


def self_test():
    checks = 0

    def check_equal(actual, expected):
        nonlocal checks
        checks += 1
        if actual != expected:
            raise RuntimeError(f"self-test check {checks}: {actual!r} != {expected!r}")

    prefix = "openlibrary/catalog/add_book/tests/test_add_book.py::TestNormalizeImportRecord::"
    test = prefix + "test_future_publication_dates_are_deleted"
    current = test + "[2026-True]"
    following = test + "[2027-False]"
    fixed = [test + "[2000-11-11-True]", test + "[9999-01-01-False]"]
    shipped = test + "[2026-01-01-True]"
    other = prefix + "test_2026_is_not_a_parameter"
    unstable = test + "[unstable]"
    absent = test + "[absent]"
    unselected = "other.py::test_date[2026]"
    reference = dict.fromkeys([current, following, *fixed, shipped, other,
                               unstable, absent, unselected], "PASSED")
    empty = {name: status for name, status in reference.items() if name != absent}
    lists, report = regenerate([reference, reference],
                               [empty, {**empty, unstable: "FAILED"}],
                               ["./openlibrary/catalog/add_book/tests/test_add_book.py"],
                               run_year=2026, shipped_names=[shipped])
    check_equal(lists, {"fail_to_pass": [absent],
                        "pass_to_pass": sorted([*fixed, shipped, other])})
    check_equal(report, {"unstable_tests": [{"name": unstable,
                                             "reference": ["PASSED", "PASSED"],
                                             "empty": ["PASSED", "FAILED"]}],
                         "date_dependent_tests": [current, following],
                         "excluded_tests": [], "dropped_shipped_tests": []})
    lists, report = regenerate([reference], [empty],
                               ["openlibrary/catalog/add_book/tests/test_add_book.py"],
                               run_year=2025)
    check_equal(current in lists["pass_to_pass"], False)
    check_equal(following in lists["pass_to_pass"], True)
    check_equal(report["date_dependent_tests"], sorted([current, shipped]))
    check_equal(decode_list("['test.py::test']", "fail_to_pass"), ["test.py::test"])
    check_equal(decode_list('["test.py::test"]', "pass_to_pass"), ["test.py::test"])
    for template, expected in [([], ["test.py::test"]),
                               ("['old.py::test']", "['test.py::test']"),
                               ('["old.py::test"]', '["test.py::test"]')]:
        check_equal(encode_list(["test.py::test"], template), expected)
        quoted = ["test.py::test[quote's]", 'test.py::test["quoted"]']
        check_equal(decode_list(encode_list(quoted, template), "tests"), quoted)

    # Different non-pass statuses and missing entries are equally stable.
    reference = {test: "PASSED", other: "FAILED"}
    lists, report = regenerate([reference, {**reference, other: "ERROR"}, {test: "PASSED"}],
                               [{test: "FAILED"}, {test: "ERROR"}, {}],
                               ["openlibrary/catalog/add_book/tests/test_add_book.py"],
                               shipped_names=[test])
    check_equal(lists, {"fail_to_pass": [test], "pass_to_pass": []})
    check_equal(report, {"unstable_tests": [], "date_dependent_tests": [],
                         "excluded_tests": [], "dropped_shipped_tests": []})
    lists, report = regenerate([{test: "PASSED"}], [{test: "FAILED"}],
                               ["openlibrary/catalog/add_book/tests/test_add_book.py"],
                               shipped_names=[test, absent, unselected], excluded_names=[test])
    check_equal(lists, {"fail_to_pass": [], "pass_to_pass": []})
    check_equal(report, {"unstable_tests": [], "date_dependent_tests": [],
                         "excluded_tests": [test],
                         "dropped_shipped_tests": sorted([test, absent, unselected])})
    for unsupported in ["suite should work", "test.js test name", "TestGo/subtest"]:
        for references, empties, shipped_names in [([{unsupported: "PASSED"}], [{}], []),
                                                   ([{}], [{unsupported: "FAILED"}], []),
                                                   ([{}], [{}], [unsupported])]:
            try:
                regenerate(references, empties, ["test.py"], shipped_names=shipped_names)
            except ValueError as exc:
                check_equal("only pytest node IDs" in str(exc) and unsupported in str(exc), True)
            else:
                raise RuntimeError("self-test accepted a non-pytest test name")
    print(f"self-test: passed ({checks} checks)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path,
                        help="source task config with selected_test_files_to_run")
    parser.add_argument("--reference", nargs="+", type=Path,
                        help="reference output.json files, in run order")
    parser.add_argument("--empty", nargs="+", type=Path,
                        help="empty-submission output.json files, in run order")
    parser.add_argument("--run-year", type=int, default=date.today().year,
                        help="calendar year of the measured runs (default: current year)")
    parser.add_argument("--exclude", action="append", default=[], metavar="NODE_ID",
                        help="exclude an exact pytest node ID and report it (repeatable; "
                             "dropping shipped IDs is an error)")
    parser.add_argument("--self-test", action="store_true", help="run built-in checks and exit")
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return
    if not args.config or not args.reference or not args.empty:
        parser.error("--config, --reference and --empty are required")
    try:
        config = json.loads(args.config.read_text())
        if not isinstance(config, dict):
            raise ValueError("config must be a JSON object")
        selected = decode_list(config["selected_test_files_to_run"], "selected_test_files_to_run")
        shipped = set().union(*(decode_list(config[field], field)
                                for field in ("fail_to_pass", "pass_to_pass")))
        lists, report = regenerate([load_run(p) for p in args.reference],
                                   [load_run(p) for p in args.empty], selected,
                                   args.run_year, shipped, args.exclude)
        for field, tests in lists.items():
            lists[field] = encode_list(tests, config[field])
        output = json.dumps(lists, indent=2) + "\n"
        report_output = json.dumps(report, indent=2) + "\n"
    except (OSError, ValueError, SyntaxError, KeyError) as exc:
        parser.error(str(exc))
    sys.stderr.write(report_output)
    if report["dropped_shipped_tests"]:
        sys.exit(1)
    sys.stdout.write(output)


if __name__ == "__main__":
    main()
