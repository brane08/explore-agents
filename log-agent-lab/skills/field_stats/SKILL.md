---
name: field_stats
description: Summary statistics for a single field across all log records.
input_schema:
  type: object
  properties:
    field:
      type: string
      description: Field to summarise (duration_ms, status_code, level, service, message).
  required:
    - field
---
Numeric fields (`duration_ms`, `status_code`) return
`{"field", "min", "max", "avg", "count"}`. Categorical fields (`level`,
`service`, `message`) return `{"field", "cardinality", "top_values"}` where
`top_values` is the ten most common values with their counts.
