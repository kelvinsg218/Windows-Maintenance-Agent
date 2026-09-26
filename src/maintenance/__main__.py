"""Permite executar o agente com ``python -m maintenance`` (e serve de entrada do PyInstaller)."""

import sys

from maintenance.cli import main

if __name__ == "__main__":
    sys.exit(main())
