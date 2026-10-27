# CLAUDE.md

QuantProof is built in Claude Code. The rules for every coding agent live in AGENTS.md;
follow them exactly.

@AGENTS.md

Before any task, read:
- docs/PROJECT_BRIEF.md — what QuantProof is, the quant correctness rules, scope, demo
- docs/BUILD_PLAN.md — today's tasks

Environment notes:
- Secrets available as environment variables: MODEL_API_KEY (Meta Model API),
  BASETEN_API_KEY (once Baseten credits arrive), SEC_USER_AGENT. Never print them.
- If a network call fails because a domain is blocked, stop and tell Joyeb which domain
  to add to the environment's allowed domains. Do not look for workarounds.
