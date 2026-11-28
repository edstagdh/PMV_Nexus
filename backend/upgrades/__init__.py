"""Upgrades of the app's data from one version to the next (3.12.0).

- steps.py: the list of upgrade steps, in order, with what each one does and how long it takes.
  Standard library only, so the installers can run it (Tools/upgrade_plan.py) with the
  installed app's Python before they replace anything.
- runner.py: runs the steps when the app starts -- a copy of the database first, quick steps
  before anything else reads the data, long ones while the app shows "Upgrading" -- and
  refuses to start an older app on data a newer one has upgraded.

A step is written once and never changed afterwards: a library can be upgraded from any older
version, so every step from then on runs in order. Every step can run twice without harm and
picks up where it stopped (a power cut, a closed window).
"""
