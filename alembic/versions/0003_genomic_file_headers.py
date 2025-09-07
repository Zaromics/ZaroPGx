"""Create genomic_file_headers table for storing parsed header JSON

Revision ID: 0003_genomic_file_headers
Revises: 0002_enable_postbio_poststat_placeholders
Create Date: 2025-09-07

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0003_genomic_file_headers"
down_revision = "0002_enable_postbio_poststat_placeholders"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Ensure uuid extension exists for uuid_generate_v4()
    op.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\";")

    # Create table in public schema for simplicity and broad accessibility
    op.create_table(
        "genomic_file_headers",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("uuid_generate_v4()"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("file_path", sa.Text(), nullable=False),
        sa.Column("file_format", sa.String(length=10), nullable=False),
        sa.Column("header_info", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "extracted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        schema=None,
    )

    # Constraints and indexes
    op.create_check_constraint(
        constraint_name="chk_genomic_file_headers_format",
        table_name="genomic_file_headers",
        condition="file_format IN ('BAM','SAM','CRAM','VCF','BCF','FASTA','FASTQ')",
        schema=None,
    )

    op.create_index(
        "idx_gfh_file_format",
        "genomic_file_headers",
        ["file_format"],
        unique=False,
        schema=None,
    )

    op.create_index(
        "idx_gfh_header_info_gin",
        "genomic_file_headers",
        [sa.text("header_info")],
        unique=False,
        postgresql_using="gin",
        schema=None,
    )


def downgrade() -> None:
    # Drop indexes then table
    op.drop_index("idx_gfh_header_info_gin", table_name="genomic_file_headers", schema=None)
    op.drop_index("idx_gfh_file_format", table_name="genomic_file_headers", schema=None)
    op.drop_constraint("chk_genomic_file_headers_format", "genomic_file_headers", type_="check", schema=None)
    op.drop_table("genomic_file_headers", schema=None)


