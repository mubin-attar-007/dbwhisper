from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, func
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
    connection_string = Column(Text, nullable=False)
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

    def __repr__(self):
        return f"<DatabaseConfig(db_flag={self.db_flag}, description={self.description})>"
