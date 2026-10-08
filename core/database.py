from sqlalchemy import CheckConstraint, Column, Integer, MetaData, Table, Text

metadata = MetaData()

tasks = Table(
    "tasks",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("objective", Text, nullable=False),
    Column("direction", Text, nullable=False),
    Column("variables_json", Text, nullable=False),
    Column("parameters_json", Text, nullable=False),
    Column("constraints_json", Text, nullable=False),
    Column("derived_metrics_json", Text, nullable=False),
    Column("is_builtin", Integer, nullable=False),
    Column("objective_label", Text, nullable=False),
    Column("objective_unit", Text, nullable=False),
    CheckConstraint("direction IN ('min', 'max')"),
    CheckConstraint("is_builtin IN (0, 1)"),
)

methods = Table(
    "methods",
    metadata,
    Column("id", Text, primary_key=True),
    Column("name", Text, nullable=False),
    Column("description", Text, nullable=False),
    Column("category", Text, nullable=False),
    Column("enabled", Integer, nullable=False),
    Column("defaults_json", Text, nullable=False),
    CheckConstraint("enabled IN (0, 1)"),
)

users = Table(
    "users",
    metadata,
    Column("id", Text, primary_key=True),
    Column("login", Text, nullable=False, unique=True),
    Column("password_hash", Text, nullable=False),
    Column("role", Text, nullable=False),
    Column("active", Integer, nullable=False, server_default="1"),
    Column("session_version", Integer, nullable=False, server_default="0"),
    CheckConstraint("role IN ('user', 'admin')"),
    CheckConstraint("active IN (0, 1)"),
)
