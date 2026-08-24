from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class User(Base):
    """An authenticated account. Owns the databases it enrolls (multi-tenancy)."""

    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    email = Column(String(320), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    is_active = Column(Boolean, nullable=False, default=True)
    is_admin = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    def __repr__(self):
        return f"<User(id={self.id}, email={self.email})>"


class UserSession(Base):
    """A server-side session. Only the SHA-256 of the opaque cookie token is stored."""

    __tablename__ = "user_sessions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    token_hash = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(
        Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)


class DatabaseConfig(Base):
    __tablename__ = "Database_config"

    id = Column(Integer, primary_key=True, autoincrement=True)
    db_flag = Column(String(100), unique=True, nullable=False, index=True)
    db_type = Column(String(50), nullable=False)
    # Legacy plaintext column, kept for one release so un-migrated rows keep working.
    # New writes go to connection_secret; see app/platform/connection_secrets.py.
    connection_string = Column(Text, nullable=False)
    connection_secret = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    max_rows = Column(Integer, nullable=False, default=1000)
    query_timeout = Column(Integer, nullable=False, default=30)
    intro_template = Column(Text, nullable=True)
    exclude_column_matches = Column(Boolean, nullable=False, default=False)
    schema_extracted = Column(Boolean, nullable=False, default=False)
    # Note: read-only enforcement flags are intentionally kept outside of the persistent
    # DB schema in `DatabaseConfig` to avoid schema migrations; administrators may
    # control read-only enforcement via application-level config or DatabaseSettings.

    schema_extraction_date = Column(
        DateTime,
        server_default=func.now(),  # applied ONLY when user doesn't pass a value
    )
    # Multi-tenancy: the owning user. NULL = public (e.g. the shared demo), readable by anyone.
    owner_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    def __repr__(self):
        return f"<DatabaseConfig(db_flag={self.db_flag}, description={self.description})>"


class VerifiedQuery(Base):
    """A human-approved question -> SQL pair used to steer future generation (the flywheel).

    Saved only on explicit user approval; the SQL is validated read-only before storing. A copy
    of the question is embedded into the pgvector collection (tagged section=verified_qsql) so the
    agent can retrieve similar approved examples at generation time.
    """

    __tablename__ = "verified_queries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    db_flag = Column(String(100), nullable=False, index=True)
    question = Column(Text, nullable=False)
    sql = Column(Text, nullable=False)
    # pgvector document id for the embedded copy, so we can delete it alongside the row.
    embedding_id = Column(String(64), nullable=True)
    owner_id = Column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # ── Governance (v2) ──────────────────────────────────────────────────────────────────────
    # A pair is only as trustworthy as the schema it was approved against. Recording the snapshot
    # and the literal-independent fingerprint is what lets a schema change mark a pair stale
    # instead of leaving it quietly wrong.
    status = Column(String(16), nullable=False, default="approved", index=True)
    snapshot_id = Column(String(128), nullable=True)
    sql_fingerprint = Column(String(64), nullable=True, index=True)
    tables = Column(JSON, nullable=True)
    dialect = Column(String(32), nullable=True)
    reviewer = Column(String(320), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)
    usage_count = Column(Integer, nullable=False, default=0)
    last_validated_at = Column(DateTime(timezone=True), nullable=True)
    staleness_reason = Column(Text, nullable=True)
    # What produced it, so a pair approved under a retired prompt can be found later.
    prompt_version = Column(String(64), nullable=True)
    model_profile = Column(String(64), nullable=True)

    def __repr__(self):
        return f"<VerifiedQuery(id={self.id}, db_flag={self.db_flag})>"
