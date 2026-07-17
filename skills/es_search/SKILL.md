---
name: es_search
description: Search log records, optionally filtered by log level.
input_schema:
  type: object
  properties:
    query_level:
      type: string
      description: Optional log level to filter by (e.g. ERROR, WARN, INFO).
    size:
      type: integer
      description: Maximum number of hits to return.
      default: 10
---
Search the mock log corpus. Returns `{"hits": [...], "total": N}` where each
hit is a log record with the fields `level`, `service`, `message`,
`timestamp`, `duration_ms`, and `status_code`. Pass `query_level` to keep only
records at that level; omit it to search across all records.
