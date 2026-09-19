#!/usr/bin/env python3
"""
<summary>
Read or stamp the version attribute of the <addon> root element.
</summary>
<remarks>
Used by build-zip.sh. Only the root element is touched: the version on the
<import addon="xbmc.python" .../> line is left alone, and the root element
may span several lines, which is why this is not a one line sed.

Usage:
    addon_version.py get <addon.xml>              print the version
    addon_version.py set <addon.xml> <version>    stamp it in place
</remarks>
"""

import re
import sys

ROOT_VERSION = re.compile(r'(<addon\b[^>]*?\bversion=")([^"]*)(")')


def read(path):
    """
    <summary>
    The text of a file decoded as UTF-8 with its line endings left exactly as they are.
    </summary>
    <param name="path">The file to read.</param>
    <returns>Its contents as a string.</returns>
    <remarks>
    newline='' matters: main writes the file back the same way, so CRLF survives a stamp.
    </remarks>
    """
    with open(path, encoding='utf-8', newline='') as handle:
        return handle.read()


def main(argv):
    """
    <summary>
    Handle the get and set subcommands.
    </summary>
    <param name="argv">sys.argv: 'get <addon.xml>' or 'set <addon.xml> <version>'.</param>
    <returns>0 on success; any other argument shape exits with the usage text from the module docstring.</returns>
    <remarks>
    set refuses a version that is not three dotted numbers and stamps only the version
    attribute on the <addon> root element, leaving every other byte of the file as it
    was. A file with no such attribute exits with a message on either subcommand.
    </remarks>
    """
    if len(argv) == 3 and argv[1] == 'get':
        match = ROOT_VERSION.search(read(argv[2]))
        if not match:
            sys.exit('addon_version: no version attribute on the <addon> root element in ' + argv[2])
        print(match.group(2))
        return 0
    if len(argv) == 4 and argv[1] == 'set':
        path, version = argv[2], argv[3]
        if not re.fullmatch(r'\d+\.\d+\.\d+', version):
            sys.exit('addon_version: version %r is not major.minor.build' % version)
        text = read(path)
        stamped, count = ROOT_VERSION.subn(lambda m: m.group(1) + version + m.group(3), text, count=1)
        if count != 1:
            sys.exit('addon_version: no version attribute on the <addon> root element in ' + path)
        with open(path, 'w', encoding='utf-8', newline='') as handle:
            handle.write(stamped)
        return 0
    sys.exit(__doc__)


if __name__ == '__main__':
    sys.exit(main(sys.argv))
