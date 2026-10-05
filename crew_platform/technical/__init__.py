"""Technical log (TechLog) persistence layer for the EFB.

The TechLog system models the *real* persistent aircraft: the aircraft's
technical state lives in this database, not in the simulator. The aircraft
adapter (later ticket) merely determines how much of that technical state
can be reflected natively inside the currently loaded simulator addon.

The storage layer itself is model-light for now: this package ships the
SQLite database, the SQLAlchemy engine, the Alembic migration baseline,
and the :class:`~crew_platform.technical.models.Aircraft` domain model.
Further TechLog domain tables (e.g. techlog entries) arrive with later
tickets.
"""
