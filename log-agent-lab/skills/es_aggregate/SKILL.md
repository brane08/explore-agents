---
name: es_aggregate
description: Aggregate log records by a categorical field.
input_schema:
  type: object
  properties:
    group_by:
      type: string
      description: Categorical field to group by (level, service, or status_code).
    metric:
      type: string
      description: Aggregation metric. Only "count" is supported.
      default: count
  required:
    - group_by
---
Group the corpus by `group_by` and count records per bucket. Returns
`{"group_by": ..., "metric": "count", "buckets": [{"key": ..., "count": N}, ...]}`
sorted by descending count. `group_by` must be one of `level`, `service`, or
`status_code`; `metric` only supports `count`.
