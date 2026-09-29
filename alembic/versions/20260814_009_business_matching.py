"""business matching MVP

Revision ID: 202608140009
Revises: 202608010008
Create Date: 2026-08-14
"""
from alembic import op
import sqlalchemy as sa
from app.modules.business_matching.models import (
    AuditLog, BusinessMatchingProfile, Conversation, ConversationParticipant,
    MatchingSession, Meeting, MeetingResource, MeetingSlot, MeetingSlotProposal,
    MeetingVenue, Message, Notification, ParticipantBlock, ParticipantReport,
)
from app.modules.iwbif.models import Company

revision = "202608140009"
down_revision = "202608010008"
branch_labels = None
depends_on = None

TABLES = [
    Company.__table__, BusinessMatchingProfile.__table__, ParticipantBlock.__table__, ParticipantReport.__table__, Conversation.__table__,
    ConversationParticipant.__table__, MatchingSession.__table__, MeetingSlot.__table__,
    MeetingVenue.__table__, MeetingResource.__table__, Meeting.__table__, Message.__table__,
    MeetingSlotProposal.__table__, Notification.__table__, AuditLog.__table__,
]

def upgrade() -> None:
    bind = op.get_bind()
    for table in TABLES:
        if table.name == "meetings":
            metadata = sa.MetaData()
            for dependency in ("events", "conversations", "participants", "meeting_slots", "meeting_resources"):
                sa.Table(dependency, metadata, sa.Column("id", sa.Uuid(), primary_key=True))
            meeting_table = sa.Table(
                "meetings",
                metadata,
                sa.Column("id", sa.Uuid(), nullable=False),
                sa.Column("event_id", sa.Uuid(), sa.ForeignKey("events.id", ondelete="CASCADE"), nullable=False),
                sa.Column("conversation_id", sa.Uuid(), sa.ForeignKey("conversations.id"), nullable=True),
                sa.Column("requester_participant_id", sa.Uuid(), sa.ForeignKey("participants.id"), nullable=False),
                sa.Column("recipient_participant_id", sa.Uuid(), sa.ForeignKey("participants.id"), nullable=False),
                sa.Column("purpose", sa.String(80), nullable=False),
                sa.Column("topic", sa.String(255), nullable=False),
                sa.Column("description", sa.Text(), nullable=True),
                sa.Column("status", sa.String(20), nullable=False),
                sa.Column("confirmed_slot_id", sa.Uuid(), sa.ForeignKey("meeting_slots.id"), nullable=True),
                sa.Column("venue_resource_id", sa.Uuid(), sa.ForeignKey("meeting_resources.id"), nullable=True),
                sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
                sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
                sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
                sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
                sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
                sa.PrimaryKeyConstraint("id"),
                sa.Index("ix_meetings_event_status", "event_id", "status"),
                sa.Index("ix_meetings_resource_slot", "venue_resource_id", "confirmed_slot_id", unique=True),
            )
            meeting_table.create(bind, checkfirst=True)
        else:
            table.create(bind, checkfirst=True)

def downgrade() -> None:
    bind = op.get_bind()
    for table in reversed(TABLES):
        table.drop(bind, checkfirst=True)
