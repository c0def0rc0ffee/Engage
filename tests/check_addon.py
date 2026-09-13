#!/usr/bin/env python3
"""
<summary>
Static checks for the Engage Kodi add-on (service.engage).
</summary>
<remarks>
Run by build-zip.sh against the staged source tree before anything is zipped
and refuses a build when anything is wrong. No Kodi is needed: the checks
parse addon.xml, compile every Python file and enforce the house rules on
the text files. Adapted from the Dreadnought screensaver's tests/check_addon.py.

Usage:
    check_addon.py            check the project folder this file lives in
    check_addon.py <root>     check another tree laid out the same way (the
                              build passes its stage)
</remarks>
"""

import os
import re
import sys
import xml.etree.ElementTree as ET

ADDON_ID = 'service.engage'
PREFIX = 'Engage'
TEXT_SUFFIXES = ('.py', '.xml', '.po', '.md', '.sh', '.txt', '.json', '.cfg', '.ini', '')
SKIP_DIRS = {'.git', '.claude', '__pycache__', 'node_modules',
             PREFIX + ' Dist', PREFIX + ' Git', PREFIX + ' App'}
# File names that look like they hold a credential. Matched case insensitively.
SECRET_NAMES = re.compile(r'cred|secret|passw|token|sftp|(^|[^a-z])ftp|^\.env|\.pem$|\.key$|\.pfx$|^id_rsa|^connection\.txt$|config\.local',
                          re.IGNORECASE)
# Token shapes that never belong in a source tree, whatever the file name.
TOKEN_SHAPES = re.compile(r'ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16}'
                          r'|xox[abprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----'
                          r'|(?i:secret|password|api[_-]?key)\s*[:=]\s*["\'][A-Za-z0-9+/=_-]{20,}["\']')
# Private and CGNAT address ranges. Example addresses in docs and tests are
# 123.123.123.x only, so any of these is a real address by definition.
PRIVATE_IP = re.compile(r'\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01])|100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7]))\.\d{1,3}\.\d{1,3}\b')

failures = []


def fail(message):
    failures.append(message)


def check(condition, message):
    if not condition:
        fail(message)


def read(path):
    with open(path, encoding='utf-8') as handle:
        return handle.read()


def check_addon_xml(root, addon):
    path = os.path.join(addon, 'addon.xml')
    check(os.path.isfile(path), 'addon.xml is missing')
    if not os.path.isfile(path):
        return
    node = ET.parse(path).getroot()
    check(node.attrib.get('id') == ADDON_ID, 'addon.xml id must be {}'.format(ADDON_ID))
    version = node.attrib.get('version', '')
    version_file = os.path.join(root, 'VERSION')
    check(os.path.isfile(version_file), 'VERSION is missing')
    if os.path.isfile(version_file):
        wanted = read(version_file).strip()
        check(version == wanted, 'addon.xml version {} does not match VERSION {}'.format(version, wanted))
    check(re.fullmatch(r'\d+\.\d+\.\d+', version) is not None, 'addon.xml version is not major.minor.build')
    check(node.attrib.get('provider-name') == 'c0def0rc0ffee', 'addon.xml provider-name must be c0def0rc0ffee')
    imports = [i.attrib.get('addon') for i in node.findall('requires/import')]
    check('xbmc.python' in imports, 'addon.xml must import xbmc.python')
    for ext in node.findall('extension'):
        library = ext.attrib.get('library')
        if library:
            check(os.path.isfile(os.path.join(addon, library)),
                  'extension library {} is missing on disk'.format(library))
    meta = node.find("extension[@point='xbmc.addon.metadata']")
    check(meta is not None, 'addon.xml has no metadata extension')
    if meta is not None:
        icon = meta.find('assets/icon')
        check(icon is not None and icon.text and os.path.isfile(os.path.join(addon, icon.text)),
              'addon.xml icon asset is missing on disk')
        check(meta.find('license') is not None, 'addon.xml has no license')


def walk_text_files(root):
    for folder, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in files:
            yield folder, name


def check_python(root):
    for folder, name in walk_text_files(root):
        if not name.endswith('.py'):
            continue
        path = os.path.join(folder, name)
        try:
            compile(read(path), path, 'exec')
        except SyntaxError as exc:
            fail('{} does not compile: line {}: {}'.format(os.path.relpath(path, root), exc.lineno, exc.msg))


def check_house_rules(root):
    dashes = ('\u2014', '\u2013')  # em dash and en dash, spelled as escapes so this file passes its own check
    for folder, name in walk_text_files(root):
        relative = os.path.relpath(os.path.join(folder, name), root)
        if SECRET_NAMES.search(name):
            fail('{} has a secret looking file name and must not ship'.format(relative))
        if os.path.splitext(name)[1] not in TEXT_SUFFIXES:
            continue
        try:
            text = read(os.path.join(folder, name))
        except (UnicodeDecodeError, OSError):
            continue
        for line_number, line in enumerate(text.splitlines(), 1):
            if any(dash in line for dash in dashes):
                fail('{}:{} contains a dash character that the house rules disallow'.format(relative, line_number))
            if 'Co-Authored-By' in line and name != 'check_addon.py':
                fail('{}:{} carries an attribution trailer'.format(relative, line_number))
            if TOKEN_SHAPES.search(line) and name != 'check_addon.py':
                fail('{}:{} looks like it holds a credential'.format(relative, line_number))
            if PRIVATE_IP.search(line) and name != 'check_addon.py':
                fail('{}:{} names a private network address (examples must be 123.123.123.x)'.format(relative, line_number))


def main(argv):
    root = os.path.abspath(argv[1]) if len(argv) > 1 else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    addon = os.path.join(root, ADDON_ID)
    check(os.path.isdir(addon), 'add-on folder {} is missing under {}'.format(ADDON_ID, root))
    if os.path.isdir(addon):
        check_addon_xml(root, addon)
    check_python(root)
    check_house_rules(root)
    if failures:
        print('FAILED: {} problem(s)'.format(len(failures)))
        for message in failures:
            print('  - ' + message)
        return 1
    print('OK: add-on checks passed')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
