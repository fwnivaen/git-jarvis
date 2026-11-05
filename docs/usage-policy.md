# Included usage policy

Jarvis is configured to use a signed-in ChatGPT/Codex account and its included allowance. Separately billed API inference and automatic paid fallback are disabled. This policy applies to normal chat, screen/image questions, Ultra, graph workflows and the prepared Linux controller. Account entitlements and usage limits still apply; local operation does not mean model inference runs offline.

## Enforcement

- `usage_policy.py` allows only `codex-cli` and `codex-app-server`. Existing API keys, configuration fields, model aliases and model-generated switch tags cannot authorize another provider. API choices remain visible as unavailable, with the reason. Legacy API transport/schema regression tests use an explicit test-only policy override; shipped application code has no setting or environment switch for it.
- Both Codex transports force `forced_login_method="chatgpt"`, `model_provider="openai"` and standard service tier, after other overrides. The app-server verifies those effective settings and rejects a custom replacement for the OpenAI provider. API-key and `OPENAI_BASE_URL` environment overrides are removed from child environments. Credentials remain in Codex's existing private store; no cache is copied or read by JARVIS.
- Before every admitted model call, the app-server checks `account/read` and a fresh `account/rateLimits/read`. A ChatGPT account, explicit `ordinaryUsageAllowed: true`, and a valid unexhausted Codex quota window are required. Missing permission/window data fails closed. A credit balance, old reset time or sparse recovery notification never grants permission.
- Mid-turn auth/quota notifications fence active calls. Typed quota/auth failures are terminal and do not trigger model escalation or an automatic retry. Existing cancellation settlement rules still apply.
- The application never purchases credits, consumes reset credits, enables automatic recharges or sends credit-request emails. No separately billed API call is made to check whether an API credit is free.
- The Deep Agents helper remains usable with its scripted fixture model only. Live calls are rejected even if `JARVIS_DEEPAGENTS_API_KEY` exists; the helper independently blocks that path. CodexBridge is an agent executor, not a raw model for a second agent loop. A supported included-usage raw-model integration would need separate implementation and compatibility evidence before Deep Agents could be enabled.

## Limits of the guarantee

ChatGPT sign-in uses the account's plan entitlements; API-key authentication uses separate API billing. Included limits vary by account and task. See [official authentication guidance](https://learn.chatgpt.com/docs/auth), [usage/pricing guidance](https://learn.chatgpt.com/docs/pricing), and the [app-server account protocol](https://learn.chatgpt.com/docs/app-server).

A quota read is an admission check, not an atomic provider-side spending reservation. Other clients can consume the shared allowance after a read, and an in-flight turn can cross a limit before a notification arrives. The installed protocol does not distinguish promotional from purchased balances or provide JARVIS an atomic “never consume purchased credits” switch. JARVIS therefore does not treat a positive credit balance as free usage or claim a provider-enforced zero-charge guarantee. Account-level purchasing/recharge settings have not been changed. Unknown cost remains unknown in evaluation records; included subscription usage is not reported as zero-cost learning.

No Oracle resources are provisioned. Private verification, actual ongoing free allowances and available capacity must be checked before deployment. Trial credits do not establish ongoing free hosting.

## Operator recovery

If sign-in expires, complete the normal private ChatGPT login in Codex, then retry. If the allowance is exhausted, wait for recovery and retry manually; the next attempt rechecks the authoritative snapshot. If usage cannot be verified, restore the supported runtime/network connection rather than using an API key as a workaround. A Linux installation requires a fresh authorized login and its own live tests; never transfer the Mac auth cache.

Live evidence and remaining acceptance gates are recorded in [the evidence ledger](upgrade-evidence.md) and [the checklist](upgrade-checklist.md).
