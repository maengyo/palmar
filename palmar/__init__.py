"""palmar — a light tool for people who keep a lot of terminals open.

The only thing it opens is a shell. It runs no model and sends nothing outside.
The contract is `docs/protocol.md`.
"""

#: Package version. Bump it on release.
__version__ = "0.1.1"

#: Daemon↔browser **protocol** version. It moves separately from the package version — the surface
#: can change while the promise stays the same, and the other way round too. It rides out on `hello`
#: and the browser compares it against its own (protocol.md "판").
#: **Bump it when:** the meaning of a frame or a field changes so an old page reads it wrong. Only
#: adding a field is not a bump — an unknown field can just be ignored.
PROTOCOL = 1
