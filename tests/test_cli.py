import subprocess
import sys
import os
import pytest


def test_cli_help():
    pytest.skip("PyQt6 CLI tests are skipped in headless environments")
