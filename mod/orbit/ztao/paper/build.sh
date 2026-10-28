#!/bin/sh
# Build ztao.pdf. Tables/figures come from data/snapshot.json via
# figures.py (offline); run collect.py first to re-measure prices.
set -e
cd "$(dirname "$0")"
python3 figures.py
pdflatex -interaction=nonstopmode -halt-on-error ztao.tex >/dev/null || { grep -A6 '^!' ztao.log; exit 1; }
bibtex ztao | grep -i -E "error|warning" || true
pdflatex -interaction=nonstopmode -halt-on-error ztao.tex >/dev/null
pdflatex -interaction=nonstopmode -halt-on-error ztao.tex | grep -E "undefined|Output written" || true
rm -f ztao.aux ztao.log ztao.out ztao.blg ztao.bbl
