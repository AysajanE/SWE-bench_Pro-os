#!/usr/bin/env python3
"""Regenerate grading lists from N reference and N empty-submission output.json files.

Only tests whose file exactly matches selected_test_files_to_run are considered
(after normalizing relative POSIX paths). A test must pass in every reference run.
fail_to_pass contains those that never pass in an empty run; pass_to_pass contains
those that pass in every empty run. Status changes within either group, including
presence/absence changes, are unstable: exclude and report them. A test absent
from every empty run is consistently not passed and may enter fail_to_pass.
Parametrized IDs containing the run's calendar year or next year in their
parameters are date-dependent: exclude and report them unless the exact ID is
already in either shipped grading list. --run-year defaults to the current year.

Usage:
    python3 v2/tooling/regenerate_test_lists.py --config tests/config.json \
        --reference ref-1/output.json ref-2/output.json \
        --empty empty-1/output.json empty-2/output.json --run-year 2026 \
        > test-lists.json 2> excluded-tests.json
    python3 v2/tooling/regenerate_test_lists.py --self-test

The source config is read only. Stdout contains sorted fail_to_pass/pass_to_pass
fields, preserving each field's list or encoded-string type and encoding style.
Stderr contains sorted unstable_tests and date_dependent_tests reports. Unstable
statuses follow input-run order; null means missing. No run execution or
config/checksum writes occur.
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


def regenerate(reference_runs, empty_runs, selected_files, run_year=None, shipped_names=()):
    if not reference_runs or len(reference_runs) != len(empty_runs):
        raise ValueError("supply the same nonzero number of reference and empty runs")
    if run_year is None:
        run_year = date.today().year
    years = (str(run_year), str(run_year + 1))
    shipped = set(shipped_names)
    selected = {posixpath.normpath(name) for name in selected_files}
    names = set().union(*reference_runs, *empty_runs)
    lists = {"fail_to_pass": [], "pass_to_pass": []}
    unstable = []
    date_dependent = []
    for name in sorted(names):
        if posixpath.normpath(name.split("::", 1)[0]) not in selected:
            continue
        reference = [run.get(name) for run in reference_runs]
        empty = [run.get(name) for run in empty_runs]
        parameters = name.split("::", 1)[-1].partition("[")[2]
        is_date_dependent = (parameters.endswith("]")
                             and any(year in parameters[:-1] for year in years)
                             and name not in shipped)
        if is_date_dependent:
            date_dependent.append(name)
        if len(set(reference)) > 1 or len(set(empty)) > 1:
            unstable.append({"name": name, "reference": reference, "empty": empty})
        elif not is_date_dependent and all(status == "PASSED" for status in reference):
            field = "pass_to_pass" if all(status == "PASSED" for status in empty) else "fail_to_pass"
            lists[field].append(name)
    return lists, {"unstable_tests": unstable, "date_dependent_tests": date_dependent}


def self_test():
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
    assert lists == {"fail_to_pass": [absent], "pass_to_pass": sorted([*fixed, shipped, other])}
    assert report == {"unstable_tests": [{"name": unstable,
                                          "reference": ["PASSED", "PASSED"],
                                          "empty": ["PASSED", "FAILED"]}],
                      "date_dependent_tests": [current, following]}
    lists, report = regenerate([reference], [empty],
                               ["openlibrary/catalog/add_book/tests/test_add_book.py"],
                               run_year=2025)
    assert current not in lists["pass_to_pass"]
    assert following in lists["pass_to_pass"]
    assert report["date_dependent_tests"] == sorted([current, shipped])
    assert decode_list("['test.py::test']", "fail_to_pass") == ["test.py::test"]
    assert decode_list('["test.py::test"]', "pass_to_pass") == ["test.py::test"]
    print("self-test: passed")


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
                                   args.run_year, shipped)
        for field, tests in lists.items():
            if isinstance(config.get(field), str):
                try:
                    json.loads(config[field])
                except ValueError:
                    lists[field] = repr(tests)
                else:
                    lists[field] = json.dumps(tests)
        output = json.dumps(lists, indent=2) + "\n"
        report_output = json.dumps(report, indent=2) + "\n"
    except (OSError, ValueError, SyntaxError, KeyError) as exc:
        parser.error(str(exc))
    sys.stdout.write(output)
    sys.stderr.write(report_output)


if __name__ == "__main__":
    main()
