"""Abre a interface com ``python -m maintenance.gui`` (também é a entrada do PyInstaller)."""

import sys

from maintenance.gui.app import main

if __name__ == "__main__":
    sys.exit(main())
