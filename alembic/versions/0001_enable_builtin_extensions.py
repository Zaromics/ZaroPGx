"""Enable built-in PostgreSQL extensions used by the app

Revision ID: 0001_enable_builtin_extensions
Revises: 
Create Date: 2025-08-27

"""
from alembic import op

revision = "0001_enable_builtin_extensions"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Note: Using IF NOT EXISTS to make idempotent
    op.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm;")
    op.execute("CREATE EXTENSION IF NOT EXISTS hstore;")
    op.execute("CREATE EXTENSION IF NOT EXISTS ltree;")
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gin;")


def downgrade() -> None:
    # Intentionally leave extensions installed (safe no-op downgrade)
    pass


