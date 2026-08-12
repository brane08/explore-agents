# cron-next-fire-times — stub-class profile variant (v1)

Profile class: deterministic stub (no external provider). The stub profile does not
perform natural-language extraction, so this variant expects the parameters to
arrive already structured and simply echoes them:

```json
{"cron_expression": "<verbatim input>", "start_timestamp": "<verbatim input>"}
```

Rules 3 and 4 of `prompts/default.md` still apply: missing input is reported, never
guessed, and no fire time is ever produced by the prompt layer.
