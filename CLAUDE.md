# CLAUDE.md

QuantProof is built in Claude Code. The rules for every coding agent live in AGENTS.md;
follow them exactly.

@AGENTS.md

Before any task, read:
- docs/PROJECT_BRIEF.md — what QuantProof is, the quant correctness rules, scope, demo
- docs/BUILD_PLAN.md — today's tasks

Environment notes:
- Meta Model API key: in the cloud environment it is a **network secret** (Bearer, host
  `api.meta.ai`, path `/v1/`). The proxy adds the `Authorization` header itself, so
  `MODEL_API_KEY` is usually NOT set there. The client sends the header only when
  `MODEL_API_KEY` is set (local runs with a `.env`, or a deployed host); otherwise it sends none
  and relies on the network secret. Never require the variable to exist.
- Other secrets, as environment variables when set: BASETEN_API_KEY (once Baseten credits
  arrive), SEC_USER_AGENT. Never print, log or commit any key.
- If a network call fails because a domain is blocked, stop and tell Joyeb which domain
  to add to the environment's allowed domains. Do not look for workarounds.
