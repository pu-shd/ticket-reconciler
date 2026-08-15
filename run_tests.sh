#!/bin/sh
# The single test command, identical locally and in CI.
set -e
echo "==> pytest"
python -m pytest -q --cov --cov-report=term-missing:skip-covered
echo "==> all green"
