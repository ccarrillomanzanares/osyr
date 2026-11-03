"""OSYR — system CLI (`osyr`), for the user and for the agent.

Every command returns a Result following the contract in docs/ARCHITECTURE.md:
{"ok", "code", "data", "hint", "confirm"}. With --json it is printed as is;
without it, as text for people.
"""
