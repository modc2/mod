"""Interaction-cost engine: a graph-based, HCI-grounded model of jcode-mobile use.

See model.py for the shared data model and the design rationale. Workers build
disjoint modules in this package against that contract:

  model.py        shared types (DONE; do not edit destructively)
  log_mining.py   mine ~/.jcode/logs to ground edge weights in REAL TUI usage
  ui_map.py       map the SwiftUI source -> UITarget geometry per screen
  cost_model.py   price one action in seconds (KLM/TLM operators + Fitts)
  user_model.py   build the weighted ActionGraph (states/actions/tasks)
  engine.py       stationary distribution + expected cost + task times
"""
