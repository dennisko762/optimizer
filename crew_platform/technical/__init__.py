"""Technical log (TechLog) persistence layer for the EFB.

The TechLog system models the *real* persistent aircraft: the aircraft's
technical state lives in this database, not in the simulator. The aircraft
adapter (later ticket) merely determines how much of that technical state
can be reflected natively inside the currently loaded simulator addon.

The storage layer itself is intentionally model-free for now: this package
ships the SQLite database, the SQLAlchemy engine, and the Alembic migration
baseline. Domain tables (aircraft, techlog entries) arrive with the TechLog
domain ticket.
"""
