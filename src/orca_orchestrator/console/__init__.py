"""Interactive console for an Orca terminal tab.

``bridge`` and ``view`` use no UI framework: they connect the existing logic
(snapshot, verification, flow, review, handoff, preflight) to the screen and
turn its structured models into text. ``yard``, ``motion``, ``pixels`` and
``sprites`` (the pixel yard's state mapping, movement and art) use no UI
framework either. Only ``app`` and ``yard_screen`` import Textual, and only
``python -m orca_orchestrator`` in an interactive terminal loads them.
"""
