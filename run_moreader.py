"""PyInstaller entry point for the packaged Windows build.

Using a plain top-level script (instead of ``python -m moreader``) keeps
PyInstaller's analysis simple and avoids relative-import edge cases when frozen.
"""

import sys

from moreader.cli import main

if __name__ == "__main__":
    sys.exit(main())
