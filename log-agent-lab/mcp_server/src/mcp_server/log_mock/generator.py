import random
import datetime

_LEVELS = ["INFO", "WARN", "ERROR", "DEBUG"]
_SERVICES = ["auth-service", "order-service", "payment-service", "notification-service", "gateway"]
_MESSAGES = {
    "INFO": ["Request completed", "Cache hit", "Health check OK", "Session started"],
    "WARN": ["Slow query detected", "Retry attempt", "High memory usage", "Rate limit approaching"],
    "ERROR": ["Connection timeout", "Unhandled exception", "Database error", "Upstream failure"],
    "DEBUG": ["Entering handler", "Payload received", "Token validated", "Cache miss"],
}

def generate_logs(count: int, seed: int | None = None) -> list[dict]:
    rng = random.Random(seed)
    base_time = datetime.datetime(2024, 1, 15, 0, 0, 0)
    records = []
    for i in range(count):
        level = rng.choice(_LEVELS)
        service = rng.choice(_SERVICES)
        message = rng.choice(_MESSAGES[level])
        ts = base_time + datetime.timedelta(seconds=i * rng.randint(1, 60))
        records.append({
            "timestamp": ts.isoformat() + "Z",
            "level": level,
            "service": service,
            "message": message,
            "duration_ms": rng.randint(5, 3000),
            "status_code": rng.choice([200, 200, 200, 400, 500, 503]),
        })
    return records
