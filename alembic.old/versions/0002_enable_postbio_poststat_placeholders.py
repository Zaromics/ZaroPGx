"""Placeholders for PostBio and PostStat extensions

Revision ID: 0002_enable_postbio_poststat_placeholders
Revises: 0001_enable_builtin_extensions
Create Date: 2025-08-27

"""
from alembic import op

revision = "0002_postbio_poststat"
down_revision = "0001_enable_builtin_extensions"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Guarded enables: will succeed only if extensions are already installed in the image
    # PostBio
    op.execute("DO $$ BEGIN PERFORM 1 FROM pg_available_extensions WHERE name='postbio'; IF FOUND THEN EXECUTE 'CREATE EXTENSION IF NOT EXISTS postbio'; END IF; END $$;")
    # PostStat
    op.execute("DO $$ BEGIN PERFORM 1 FROM pg_available_extensions WHERE name='poststat'; IF FOUND THEN EXECUTE 'CREATE EXTENSION IF NOT EXISTS poststat'; END IF; END $$;")


def downgrade() -> None:
    # Keep extensions if present; no destructive downgrade
    pass


