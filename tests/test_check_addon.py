#!/usr/bin/env python3
"""
<summary>
Self test for the house rules gate (tests/check_addon.py): a dash written as an
escape in any of its forms is reported, a literal dash is reported, and a Python
construct newer than the oldest Python the add-on runs on fails the compile
check even though the build box's own Python accepts it.
</summary>
<remarks>
Run by build-zip.sh before anything is zipped; exits non zero on the first
failure reported. The gate is imported from the file beside this one and run
against a small tree written into a temporary folder. The offending strings are
built by concatenation so that this file, which the gate also scans, carries
none of them itself.

Usage:
    python3 tests/test_check_addon.py
</remarks>
"""

import importlib.util
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))

spec = importlib.util.spec_from_file_location('check_addon', os.path.join(HERE, 'check_addon.py'))
check_addon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_addon)

FAILURES = []


def check(condition, message):
    """
    <summary>Record a failure when the condition is false; the run carries on.</summary>
    <param name="condition">What must hold.</param>
    <param name="message">What to report when it does not.</param>
    """
    if not condition:
        FAILURES.append(message)
        print('FAIL: {}'.format(message))


def write(root, name, text):
    """<summary>Write one text file into the temporary tree.</summary>"""
    with open(os.path.join(root, name), 'w', encoding='utf-8') as handle:
        handle.write(text)


def gate_failures(root, *checks):
    """
    <summary>
    Run the given check functions of the gate against root and return what they
    reported, leaving the gate's own list empty again.
    </summary>
    <param name="root">The tree to check.</param>
    <param name="checks">check_addon functions taking the root.</param>
    <returns>The failure messages, in order.</returns>
    """
    del check_addon.failures[:]
    for fn in checks:
        fn(root)
    found = list(check_addon.failures)
    del check_addon.failures[:]
    return found


def test_escaped_dashes():
    """<summary>Every escaped form of a long dash is reported, by file and line.</summary>"""
    root = tempfile.mkdtemp(prefix='engage-gate-test-')
    backslash = chr(92)
    write(root, 'entity.md', 'one\ntwo &' + 'mdash; three\n')
    write(root, 'escape.py', "x = 'a " + backslash + "u2014 b'\n")
    write(root, 'hexcap.html', '<p>&#' + 'X2014; and &#' + 'x2013;</p>\n')
    write(root, 'long.js', "var s = '" + backslash + "U00002014';\n")
    write(root, 'decimal.css', 'content: "&#' + '8211;";\n')
    write(root, 'literal.txt', 'a ' + chr(0x2014) + ' b\n')
    write(root, 'clean.py', "y = 'no dashes here'\n")
    found = gate_failures(root, check_addon.check_house_rules)
    for name, line in (('entity.md', 2), ('escape.py', 1), ('hexcap.html', 1), ('long.js', 1), ('decimal.css', 1)):
        check(any(f.startswith('{}:{} writes a dash as an escape'.format(name, line)) for f in found),
              '{} line {} reported as an escaped dash: {}'.format(name, line, found))
    check(any(f.startswith('literal.txt:1 contains a dash character') for f in found),
          'a literal dash is still reported')
    check(not any(f.startswith('clean.py') for f in found), 'a clean file is not reported')


def test_oldest_python():
    """<summary>A construct newer than OLDEST_PYTHON fails the compile check on a newer box.</summary>"""
    root = tempfile.mkdtemp(prefix='engage-gate-test-')
    write(root, 'newer.py', 'def f(x):\n    match x:\n        case 1:\n            return 1\n    return 0\n')
    write(root, 'fine.py', 'def f(x):\n    if x == 1:\n        return 1\n    return 0\n')
    write(root, 'broken.py', 'def f(:\n')
    found = gate_failures(root, check_addon.check_python)
    check(any(f.startswith('newer.py needs a newer Python than 3.8') for f in found),
          'a match statement is refused as newer than 3.8: {}'.format(found))
    check(any(f.startswith('broken.py does not compile') for f in found), 'a syntax error is still reported')
    check(not any(f.startswith('fine.py') for f in found), 'plain code passes')


def main():
    """<summary>Run every test and exit non zero if any check failed.</summary>"""
    for test in (test_escaped_dashes, test_oldest_python):
        print('running {}'.format(test.__name__))
        test()
    if FAILURES:
        print('{} check(s) failed'.format(len(FAILURES)))
        return 1
    print('test_check_addon: all checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main())
