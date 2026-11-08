# GuardAId backend and local Dashboard

See the repository-root README for prerequisites and private configuration.
`app/` contains FastAPI routes, services, SQLite persistence, inference adapters
and static Dashboard assets; `migrations/` contains schema definitions, not
operational account or incident records. Keep `runtime_data/` local and ignored.

The operator Dashboard is bound to loopback in the documented startup command.
Protected Mobile API endpoints use separately configured Firebase identity and
SQLite role/permission records. Do not expose local operator endpoints directly
to an untrusted network. Supply Firebase configuration and model files privately.

Scene Description and binary Borderline Review are separate inference tasks.
Normal notifications follow the initial detector/classifier eligibility path;
binary verification affects supplementary eligibility and does not retract
already-delivered notifications. All incident decisions remain subject to
human review.
