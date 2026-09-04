"""ML / AI components.

The components in this package are decoupled from the main strategy:
- Interaction goes through files / SQLite only; no deep in-process coupling
- Dependencies are optional: if any ML component is missing, the main flow still runs
"""
