"""Init: create schemas users and diary.

Revision ID: 0001
Revises:
Create Date: 2026-06-03 00:00:00.000000 UTC

C1 baseline (data-model §7): creates the two top-level Postgres schemas that
will own all business tables in later stages.  No tables are created here —
this revision only establishes the schema namespace.

upgrade : CREATE SCHEMA IF NOT EXISTS users; CREATE SCHEMA IF NOT EXISTS diary;
downgrade: DROP SCHEMA IF EXISTS users; DROP SCHEMA IF EXISTS diary;

Reversibility: both schemas are empty at this stage, so DROP is safe.
Cross-schema FK check: none introduced (no tables, no FK at all).
"""

from collections.abc import Sequence

from alembic import op

# ---------------------------------------------------------------------------
# Revision identifiers — used by Alembic to build the migration graph.
# ---------------------------------------------------------------------------
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create schemas `users` and `diary` (baseline, no tables)."""
    op.execute("CREATE SCHEMA IF NOT EXISTS users")
    op.execute("CREATE SCHEMA IF NOT EXISTS diary")


def downgrade() -> None:
    """Drop schemas `users` and `diary`.

    Safe because both schemas are empty at this revision — no tables, no
    objects depend on them.  CASCADE is omitted intentionally: if a later
    migration accidentally leaves objects behind, the DROP will fail loudly
    rather than silently destroy data.
    """
    op.execute("DROP SCHEMA IF EXISTS diary")
    op.execute("DROP SCHEMA IF EXISTS users")
