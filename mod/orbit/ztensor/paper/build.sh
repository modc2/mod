#!/bin/sh
# Build ztensor.pdf. Tables/figures come from data/snapshot.json via
# figures.py (offline); run collect.py first to re-measure prices.
set -e
cd "$(dirname "$0")"
python3 figures.py
pdflatex -interaction=nonstopmode -halt-on-error ztensor.tex >/dev/null || { grep -A6 '^!' ztensor.log; exit 1; }
bibtex ztensor | grep -i -E "error|warning" || true
pdflatex -interaction=nonstopmode -halt-on-error ztensor.tex >/dev/null
pdflatex -interaction=nonstopmode -halt-on-error ztensor.tex | grep -E "undefined|Output written" || true
rm -f ztensor.aux ztensor.log ztensor.out ztensor.blg ztensor.bbl
