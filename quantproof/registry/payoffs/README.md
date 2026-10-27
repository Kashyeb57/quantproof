# Registered payoffs

This directory holds agent-written payoff code that has passed the full
proof battery in `quantproof/validation/synth_battery.py`.

How code gets in: `quantproof.registry.store.register` writes a
`<name>.py` file (the payoff code verbatim) and a `<name>.json` metadata
file (SHA-256 of the code, model, creation date, issuer, CUSIP,
unsupported features and the battery results). Registration refuses code
whose battery did not pass in full, code that fails the sandbox static
check, and names that already exist. `store.load` re-checks the SHA-256,
so a payoff file changed after registration is refused.

Registered code is still untrusted: it is only ever run through the
sandbox in `quantproof/registry/sandbox.py` (a subprocess with a timeout,
no network and an import allow-list), never imported or executed in the
host process.
