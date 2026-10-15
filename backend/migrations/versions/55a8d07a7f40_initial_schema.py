"""initial schema

Revision ID: 55a8d07a7f40
Revises:
Create Date: 2026-10-15 16:25:48.480037

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy import Text
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = '55a8d07a7f40'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str]] = None
depends_on: Union[str, Sequence[str]] = None


_ALL_ENUMS = [
    ('usertype', ['individual', 'educator', 'team', 'student']),
    ('sourcetype', ['demo', 'licensed', 'user_uploaded']),
    ('researchtype', ['quantitative', 'fundamental']),
    ('experimentstatus', ['draft', 'exploring', 'locked', 'archived']),
    ('visibility', ['private', 'shared', 'public']),
    ('hypothesisstatus', ['untested', 'evidence_emerging', 'insufficient_evidence', 'contradicted', 'supported', 'partially_supported']),
    ('costtype', ['ai_inference', 'compute', 'data', 'storage', 'support_time', 'other']),
    ('costsource', ['actual', 'estimate']),
    ('modelstatus', ['draft', 'researching', 'historical_test_complete', 'locked_for_simulation', 'forward_observation', 'evidence_insufficient', 'retired', 'revoked']),
    ('modeltype', ['rule_based', 'prompt_based_research_assistant', 'future_external_model_reference']),
    ('memorytype', ['explicit_preference', 'approved_constraint', 'recurring_methodology_issue', 'reflection', 'accepted_ai_draft', 'rejected_ai_draft']),
    ('privacyscope', ['private_only', 'approved_for_personal_ai']),
    ('intendedusertype', ['individual_researcher', 'educator', 'creator', 'ai_builder', 'small_research_team']),
    ('pilotapplicationstatus', ['submitted', 'approved', 'rejected', 'waitlisted']),
    ('supporttier', ['standard', 'priority', 'dedicated']),
    ('resolutionstatus', ['open', 'in_progress', 'resolved', 'wont_fix']),
    ('usageeventtype', ['signup_completed', 'pilot_application_submitted', 'first_experiment_created', 'first_run_completed', 'strategy_locked', 'forward_simulation_started', 'paper_simulation_revisited', 'second_experiment_created', 'ai_draft_approved', 'export_generated', 'support_request_created']),
    ('inferencerunstatus', ['pending', 'running', 'completed', 'failed']),
    ('simulationstatus', ['pending', 'active', 'paused', 'completed', 'retired']),
    ('draftstatus', ['DRAFT', 'PENDING_REVIEW', 'ACCEPTED', 'REJECTED']),
    ('portfoliostatus', ['active', 'paused', 'retired', 'completed']),
    ('exportstatus', ['pending', 'generating', 'completed', 'failed']),
    ('evaluationtype', ['historical', 'forward_simulation', 'methodology_review', 'behavior_consistency']),
    ('evaluatortype', ['system', 'human', 'future_ai_assistant']),
    ('confidencelevel', ['low', 'medium', 'high', 'not_provided']),
    ('outcomestatus', ['pending', 'evaluated', 'expired']),
    ('paperdecisionactortype', ['human', 'ai_draft', 'human_approved_ai_draft', 'system']),
    ('paperdecisiontype', ['no_action', 'entry', 'exit', 'pause', 'retire', 'override_request']),
    ('decisiontype', ['no_action', 'simulated_entry', 'simulated_exit', 'forced_exit', 'blocked_action']),
    ('intentordertype', ['MARKET', 'LIMIT']),
    ('intentside', ['BUY', 'SELL']),
    ('intentstatus', ['pending', 'authorized', 'submitted', 'filled', 'cancelled', 'rejected', 'uncertain']),
    ('fillsource', ['simulated', 'broker']),
    ('orderside', ['BUY', 'SELL']),
    ('orderstatus', ['pending', 'filled', 'cancelled', 'rejected']),
    ('ordertype', ['market', 'limit']),
    ('riskcheckstatus', ['passed', 'failed', 'override_requested']),
    ('positionstatus', ['open', 'closed']),
    ('runstatus', ['pending', 'running', 'completed', 'failed']),
    ('trainingjobstatus', ['pending', 'running', 'completed', 'failed', 'cancelled']),
    ('killswitchscope', ['global', 'training', 'execution', 'inference']),
]


def _create_enums(bind) -> None:
    for name, values in _ALL_ENUMS:
        enum_type = sa.Enum(*values, name=name)
        enum_type.create(bind, checkfirst=True)


def _drop_enums(bind) -> None:
    for name, _values in _ALL_ENUMS:
        op.execute(f"DROP TYPE IF EXISTS {name}")


def upgrade() -> None:
    # ── Phase 1: tables with no FK dependencies ──────────────────────────
    op.create_table('users',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('display_name', sa.String(length=255), nullable=True),
    sa.Column('user_type', sa.Enum('individual', 'educator', 'team', 'student', name='usertype'), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('is_admin', sa.Boolean(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)

    op.create_table('datasets',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('provider', sa.String(length=255), nullable=False),
    sa.Column('source_type', sa.Enum('demo', 'licensed', 'user_uploaded', name='sourcetype'), nullable=True),
    sa.Column('license_status', sa.String(length=255), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('plans',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('monthly_price', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=10), nullable=False),
    sa.Column('features', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('name')
    )

    op.create_table('product_hypotheses',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('statement', sa.Text(), nullable=False),
    sa.Column('status', sa.Enum('untested', 'evidence_emerging', 'insufficient_evidence', 'contradicted', name='hypothesisstatus'), nullable=False),
    sa.Column('evidence_criteria', sa.Text(), nullable=True),
    sa.Column('evidence_notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('webhook_event_records',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('event_id', sa.String(length=255), nullable=False),
    sa.Column('event_type', sa.String(length=100), nullable=False),
    sa.Column('payload', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('processed', sa.Boolean(), nullable=False),
    sa.Column('processed_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_webhook_event_records_event_id'), 'webhook_event_records', ['event_id'], unique=True)

    # ── Phase 2: tables depending only on Phase 1 ────────────────────────
    op.create_table('workspaces',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('api_keys',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('name', sa.String(length=255), nullable=False),
    sa.Column('key_hash', sa.String(length=64), nullable=False),
    sa.Column('key_prefix', sa.String(length=8), nullable=False),
    sa.Column('scopes', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('last_used_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_api_keys_key_hash'), 'api_keys', ['key_hash'], unique=True)
    op.create_index(op.f('ix_api_keys_user_id'), 'api_keys', ['user_id'], unique=False)

    op.create_table('audit_events',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('entity_type', sa.String(length=50), nullable=False),
    sa.Column('entity_id', sa.String(length=36), nullable=False),
    sa.Column('event_type', sa.String(length=50), nullable=False),
    sa.Column('actor_id', sa.UUID(), nullable=True),
    sa.Column('actor_type', sa.String(length=10), nullable=False),
    sa.Column('payload_json', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('recorded_before_outcome', sa.Boolean(), nullable=False),
    sa.Column('rules_locked', sa.Boolean(), nullable=False),
    sa.Column('data_cutoff_at', sa.String(length=10), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_audit_events_created_at'), 'audit_events', ['created_at'], unique=False)

    op.create_table('cost_records',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=True),
    sa.Column('cost_type', sa.Enum('ai_inference', 'compute', 'data', 'storage', 'support_time', 'other', name='costtype'), nullable=False),
    sa.Column('amount', sa.Float(), nullable=False),
    sa.Column('currency', sa.String(length=10), nullable=False),
    sa.Column('source', sa.Enum('actual', 'estimate', name='costsource'), nullable=False),
    sa.Column('method_of_estimation', sa.Text(), nullable=True),
    sa.Column('linked_entity', sa.String(length=255), nullable=True),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('recorded_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_cost_records_cost_type'), 'cost_records', ['cost_type'], unique=False)
    op.create_index(op.f('ix_cost_records_owner_id'), 'cost_records', ['owner_id'], unique=False)

    op.create_table('dataset_snapshots',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('dataset_id', sa.UUID(), nullable=False),
    sa.Column('snapshot_date', sa.Date(), nullable=False),
    sa.Column('data_file_path', sa.String(length=500), nullable=False),
    sa.Column('checksum', sa.String(length=64), nullable=True),
    sa.Column('corporate_actions_adjusted', sa.Boolean(), nullable=True),
    sa.Column('missing_data_notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['dataset_id'], ['datasets.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('execution_agents',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('agent_type', sa.String(length=100), nullable=False),
    sa.Column('config_encrypted', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('model_research_records',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('purpose', sa.Text(), nullable=False),
    sa.Column('status', sa.Enum('draft', 'researching', 'historical_test_complete', 'locked_for_simulation', 'forward_observation', 'evidence_insufficient', 'retired', 'revoked', name='modelstatus'), nullable=False),
    sa.Column('allowed_asset_universe', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('allowed_research_actions', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('personal_research_memories',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('memory_type', sa.Enum('explicit_preference', 'approved_constraint', 'recurring_methodology_issue', 'reflection', 'accepted_ai_draft', 'rejected_ai_draft', name='memorytype'), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('source_entity_type', sa.String(length=50), nullable=True),
    sa.Column('source_entity_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('user_confirmed_at', sa.DateTime(), nullable=True),
    sa.Column('active', sa.Boolean(), nullable=False),
    sa.Column('privacy_scope', sa.Enum('private_only', 'approved_for_personal_ai', name='privacyscope'), nullable=False),
    sa.Column('general_model_training_opt_in', sa.Boolean(), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('pilot_applications',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('intended_user_type', sa.Enum('individual_researcher', 'educator', 'creator', 'ai_builder', 'small_research_team', name='intendedusertype'), nullable=False),
    sa.Column('current_research_workflow', sa.Text(), nullable=True),
    sa.Column('tools_currently_used', sa.Text(), nullable=True),
    sa.Column('primary_pain_point', sa.Text(), nullable=True),
    sa.Column('expected_usage_frequency', sa.String(length=100), nullable=True),
    sa.Column('willingness_to_interview', sa.Boolean(), nullable=False),
    sa.Column('consent_to_contact', sa.Boolean(), nullable=False),
    sa.Column('status', sa.Enum('submitted', 'approved', 'rejected', 'waitlisted', name='pilotapplicationstatus'), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_pilot_applications_user_id'), 'pilot_applications', ['user_id'], unique=False)

    op.create_table('pilot_entitlements',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('pilot_access', sa.Boolean(), nullable=False),
    sa.Column('research_run_limit', sa.Integer(), nullable=False),
    sa.Column('ai_usage_limit', sa.Integer(), nullable=False),
    sa.Column('export_limit', sa.Integer(), nullable=False),
    sa.Column('forward_simulation_limit', sa.Integer(), nullable=False),
    sa.Column('expiry_date', sa.DateTime(), nullable=True),
    sa.Column('support_tier', sa.Enum('standard', 'priority', 'dedicated', name='supporttier'), nullable=False),
    sa.Column('granted_by', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['granted_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_pilot_entitlements_user_id'), 'pilot_entitlements', ['user_id'], unique=True)

    op.create_table('quota_counters',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('quota_type', sa.String(length=100), nullable=False),
    sa.Column('period', sa.String(length=20), nullable=False),
    sa.Column('used', sa.Integer(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'quota_type', 'period', name='uq_quota_counter')
    )
    op.create_index(op.f('ix_quota_counters_user_id'), 'quota_counters', ['user_id'], unique=False)
    op.create_index('ix_quota_user_type', 'quota_counters', ['user_id', 'quota_type'], unique=False)

    op.create_table('subscriptions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('plan_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('payment_provider_id', sa.String(length=255), nullable=True),
    sa.Column('current_period_start', sa.DateTime(), nullable=True),
    sa.Column('current_period_end', sa.DateTime(), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['plan_id'], ['plans.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_subscriptions_user_id'), 'subscriptions', ['user_id'], unique=True)

    op.create_table('support_logs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('category', sa.String(length=100), nullable=False),
    sa.Column('time_spent_minutes', sa.Integer(), nullable=False),
    sa.Column('resolution_status', sa.Enum('open', 'in_progress', 'resolved', 'wont_fix', name='resolutionstatus'), nullable=False),
    sa.Column('linked_entity', sa.String(length=255), nullable=True),
    sa.Column('recurring_issue_tag', sa.String(length=100), nullable=True),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_support_logs_category'), 'support_logs', ['category'], unique=False)
    op.create_index(op.f('ix_support_logs_recurring_issue_tag'), 'support_logs', ['recurring_issue_tag'], unique=False)
    op.create_index(op.f('ix_support_logs_user_id'), 'support_logs', ['user_id'], unique=False)

    op.create_table('usage_events',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('event_type', sa.Enum('signup_completed', 'pilot_application_submitted', 'first_experiment_created', 'first_run_completed', 'strategy_locked', 'forward_simulation_started', 'paper_simulation_revisited', 'second_experiment_created', 'ai_draft_approved', 'export_generated', 'support_request_created', name='usageeventtype'), nullable=False),
    sa.Column('metadata_json', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_usage_events_event_type'), 'usage_events', ['event_type'], unique=False)
    op.create_index(op.f('ix_usage_events_user_id'), 'usage_events', ['user_id'], unique=False)

    # ── Phase 3: break FK cycles — create without deferred FKs ───────────
    # experiments: omit current_version_id FK (→ experiment_versions, not yet created)
    op.create_table('experiments',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('workspace_id', sa.UUID(), nullable=True),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('original_idea', sa.Text(), nullable=True),
    sa.Column('research_type', sa.Enum('quantitative', 'fundamental', name='researchtype'), nullable=True),
    sa.Column('hypothesis', sa.Text(), nullable=True),
    sa.Column('falsification_conditions', sa.Text(), nullable=True),
    sa.Column('benchmark', sa.String(length=50), nullable=True),
    sa.Column('scope', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('status', sa.Enum('draft', 'exploring', 'locked', 'archived', name='experimentstatus'), nullable=True),
    sa.Column('visibility', sa.Enum('private', 'shared', 'public', name='visibility'), nullable=True),
    sa.Column('current_version_id', sa.UUID(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['workspace_id'], ['workspaces.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    # experiment_versions: includes experiment_id FK (experiments now exists)
    op.create_table('experiment_versions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('experiment_id', sa.UUID(), nullable=False),
    sa.Column('version_number', sa.Integer(), nullable=False),
    sa.Column('strategy_spec', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('assumptions', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('change_reason', sa.Text(), nullable=True),
    sa.Column('created_by', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('user_approved_at', sa.DateTime(), nullable=True),
    sa.Column('config_hash', sa.String(length=64), nullable=True),
    sa.Column('locked_at', sa.DateTime(), nullable=True),
    sa.Column('locked_by', sa.UUID(), nullable=True),
    sa.Column('lock_reason', sa.Text(), nullable=True),
    sa.Column('strategy_config_hash', sa.String(length=64), nullable=True),
    sa.Column('dataset_cutoff_at', sa.String(length=10), nullable=True),
    sa.Column('engine_version', sa.String(length=50), nullable=True),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['experiment_id'], ['experiments.id'], ),
    sa.ForeignKeyConstraint(['locked_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    # model_cards: omit model_version_id FK (→ model_versions, not yet created)
    op.create_table('model_cards',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('model_version_id', sa.UUID(), nullable=False),
    sa.Column('purpose', sa.Text(), nullable=False),
    sa.Column('intended_research_use', sa.Text(), nullable=False),
    sa.Column('excluded_use', sa.Text(), nullable=False),
    sa.Column('data_sources', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('data_cutoff', sa.String(length=10), nullable=False),
    sa.Column('linked_experiments', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('evaluation_evidence', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('known_limitations', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('risk_flags', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('version_history', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('revocation_process', sa.Text(), nullable=False),
    sa.Column('disclaimer', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )

    # model_versions: includes model_research_record_id FK; omit model_card_id FK
    op.create_table('model_versions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('model_research_record_id', sa.UUID(), nullable=False),
    sa.Column('version_number', sa.Integer(), nullable=False),
    sa.Column('model_type', sa.Enum('rule_based', 'prompt_based_research_assistant', 'future_external_model_reference', name='modeltype'), nullable=False),
    sa.Column('model_specification_json', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('prompt_version', sa.String(length=50), nullable=True),
    sa.Column('input_schema_version', sa.String(length=50), nullable=True),
    sa.Column('output_schema_version', sa.String(length=50), nullable=True),
    sa.Column('linked_experiment_version_ids', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('linked_dataset_snapshot_ids', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('linked_simulation_session_ids', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('locked_at', sa.DateTime(), nullable=True),
    sa.Column('retired_at', sa.DateTime(), nullable=True),
    sa.Column('revoked_at', sa.DateTime(), nullable=True),
    sa.Column('revocation_reason', sa.Text(), nullable=True),
    sa.Column('model_card_id', sa.UUID(), nullable=True),
    sa.ForeignKeyConstraint(['model_research_record_id'], ['model_research_records.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    # ── Phase 4: add deferred FK constraints for cycles ──────────────────
    op.create_foreign_key('fk_experiments_current_version_id', 'experiments', 'experiment_versions', ['current_version_id'], ['id'])
    op.create_foreign_key('fk_model_cards_model_version_id', 'model_cards', 'model_versions', ['model_version_id'], ['id'])
    op.create_foreign_key('fk_model_versions_model_card_id', 'model_versions', 'model_cards', ['model_card_id'], ['id'])

    # ── Phase 5: remaining tables (all deps now satisfied) ───────────────
    op.create_table('inference_runs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('model_version_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('input_spec', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('output_signals', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('status', sa.Enum('pending', 'running', 'completed', 'failed', name='inferencerunstatus'), nullable=False),
    sa.Column('error_details', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['model_version_id'], ['model_versions.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('payment_records',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('subscription_id', sa.UUID(), nullable=True),
    sa.Column('amount', sa.Integer(), nullable=False),
    sa.Column('currency', sa.String(length=10), nullable=False),
    sa.Column('status', sa.String(length=20), nullable=False),
    sa.Column('payment_intent_id', sa.String(length=255), nullable=True),
    sa.Column('provider', sa.String(length=50), nullable=False),
    sa.Column('test_mode', sa.Boolean(), nullable=False),
    sa.Column('metadata_json', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['subscription_id'], ['subscriptions.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_payment_records_user_id'), 'payment_records', ['user_id'], unique=False)
    op.create_index('ix_payment_user_status', 'payment_records', ['user_id', 'status'], unique=False)

    op.create_table('research_drafts',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('experiment_id', sa.UUID(), nullable=True),
    sa.Column('title', sa.String(length=500), nullable=False),
    sa.Column('hypothesis', sa.Text(), nullable=True),
    sa.Column('falsification_criteria', sa.Text(), nullable=True),
    sa.Column('strategy_template', sa.String(length=100), nullable=True),
    sa.Column('asset_symbol', sa.String(length=50), nullable=True),
    sa.Column('benchmark_symbol', sa.String(length=50), nullable=True),
    sa.Column('initial_cash', sa.String(length=50), nullable=True),
    sa.Column('commission_rate', sa.String(length=50), nullable=True),
    sa.Column('slippage_rate', sa.String(length=50), nullable=True),
    sa.Column('research_rules', sa.JSON(), nullable=True),
    sa.Column('status', sa.Enum('DRAFT', 'PENDING_REVIEW', 'ACCEPTED', 'REJECTED', name='draftstatus'), nullable=False),
    sa.Column('assistant_questions', sa.JSON(), nullable=True),
    sa.Column('assistant_responses', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['experiment_id'], ['experiments.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_research_drafts_experiment_id'), 'research_drafts', ['experiment_id'], unique=False)
    op.create_index(op.f('ix_research_drafts_user_id'), 'research_drafts', ['user_id'], unique=False)

    op.create_table('research_notes',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('experiment_id', sa.UUID(), nullable=False),
    sa.Column('version_id', sa.UUID(), nullable=True),
    sa.Column('author_id', sa.UUID(), nullable=False),
    sa.Column('content', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['experiment_id'], ['experiments.id'], ),
    sa.ForeignKeyConstraint(['version_id'], ['experiment_versions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('simulation_sessions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('experiment_version_id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('initial_virtual_cash', sa.Float(), nullable=False),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('data_cutoff_at', sa.String(length=10), nullable=False),
    sa.Column('current_snapshot_date', sa.String(length=10), nullable=True),
    sa.Column('status', sa.Enum('pending', 'active', 'paused', 'completed', 'retired', name='simulationstatus'), nullable=True),
    sa.Column('simulation_policy_version', sa.String(length=20), nullable=False),
    sa.Column('paper_equity', sa.Float(), nullable=True),
    sa.Column('current_position_json', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('warnings', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('total_decisions', sa.Integer(), nullable=True),
    sa.Column('observed_days', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['experiment_version_id'], ['experiment_versions.id'], ),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('training_jobs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('experiment_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.Enum('pending', 'running', 'completed', 'failed', 'cancelled', name='trainingjobstatus'), nullable=False),
    sa.Column('parameter_grid', postgresql.JSONB(astext_type=Text()), nullable=False),
    sa.Column('strategy_spec_snapshot', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('results', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('best_params', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('error_details', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.Column('cancelled_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['experiment_id'], ['experiments.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('decision_records',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('simulation_session_id', sa.UUID(), nullable=True),
    sa.Column('actor_type', sa.Enum('human', 'ai_draft', 'human_approved_ai_draft', 'system', name='paperdecisionactortype'), nullable=False),
    sa.Column('decision_type', sa.Enum('no_action', 'entry', 'exit', 'pause', 'retire', 'override_request', name='paperdecisiontype'), nullable=False),
    sa.Column('decision_time', sa.DateTime(), nullable=False),
    sa.Column('data_cutoff_at', sa.String(length=10), nullable=True),
    sa.Column('linked_experiment_version_id', sa.UUID(), nullable=True),
    sa.Column('linked_model_research_record_id', sa.UUID(), nullable=True),
    sa.Column('rationale', sa.Text(), nullable=True),
    sa.Column('expected_outcome', sa.Text(), nullable=True),
    sa.Column('expected_timeframe_days', sa.Integer(), nullable=True),
    sa.Column('self_reported_confidence', sa.Enum('low', 'medium', 'high', 'not_provided', name='confidencelevel'), nullable=True),
    sa.Column('falsification_condition', sa.Text(), nullable=True),
    sa.Column('risk_flags', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('outcome_status', sa.Enum('pending', 'evaluated', 'expired', name='outcomestatus'), nullable=True),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['simulation_session_id'], ['simulation_sessions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('model_evaluations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('model_version_id', sa.UUID(), nullable=False),
    sa.Column('model_research_record_id', sa.UUID(), nullable=False),
    sa.Column('evaluation_type', sa.Enum('historical', 'forward_simulation', 'methodology_review', 'behavior_consistency', name='evaluationtype'), nullable=False),
    sa.Column('result_summary', sa.Text(), nullable=True),
    sa.Column('metrics_json', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('warning_flags', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('evaluation_date', sa.DateTime(), nullable=False),
    sa.Column('data_cutoff_at', sa.String(length=10), nullable=False),
    sa.Column('engine_version', sa.String(length=50), nullable=False),
    sa.Column('evaluator', sa.Enum('system', 'human', 'future_ai_assistant', name='evaluatortype'), nullable=False),
    sa.Column('methodology_version', sa.String(length=50), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['model_research_record_id'], ['model_research_records.id'], ),
    sa.ForeignKeyConstraint(['model_version_id'], ['model_versions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('model_research_exports',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('model_research_record_id', sa.UUID(), nullable=False),
    sa.Column('model_version_id', sa.UUID(), nullable=False),
    sa.Column('status', sa.Enum('pending', 'generating', 'completed', 'failed', name='exportstatus'), nullable=False),
    sa.Column('include_private_notes', sa.Boolean(), nullable=False),
    sa.Column('package_json', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('package_checksum', sa.String(length=64), nullable=True),
    sa.Column('error_message', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['model_research_record_id'], ['model_research_records.id'], ),
    sa.ForeignKeyConstraint(['model_version_id'], ['model_versions.id'], ),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('paper_portfolios',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('owner_id', sa.UUID(), nullable=False),
    sa.Column('simulation_session_id', sa.UUID(), nullable=True),
    sa.Column('currency', sa.String(length=3), nullable=False),
    sa.Column('initial_virtual_cash', sa.Float(), nullable=False),
    sa.Column('current_virtual_cash', sa.Float(), nullable=False),
    sa.Column('status', sa.Enum('active', 'paused', 'retired', 'completed', name='portfoliostatus'), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('closed_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['owner_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['simulation_session_id'], ['simulation_sessions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('research_draft_audit_events',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('draft_id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('event_type', sa.String(length=100), nullable=False),
    sa.Column('event_data', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['draft_id'], ['research_drafts.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_research_draft_audit_events_draft_id'), 'research_draft_audit_events', ['draft_id'], unique=False)
    op.create_index(op.f('ix_research_draft_audit_events_user_id'), 'research_draft_audit_events', ['user_id'], unique=False)

    op.create_table('research_draft_revisions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('draft_id', sa.UUID(), nullable=False),
    sa.Column('revision_number', sa.String(length=50), nullable=False),
    sa.Column('title', sa.String(length=500), nullable=True),
    sa.Column('hypothesis', sa.Text(), nullable=True),
    sa.Column('falsification_criteria', sa.Text(), nullable=True),
    sa.Column('strategy_template', sa.String(length=100), nullable=True),
    sa.Column('asset_symbol', sa.String(length=50), nullable=True),
    sa.Column('benchmark_symbol', sa.String(length=50), nullable=True),
    sa.Column('initial_cash', sa.String(length=50), nullable=True),
    sa.Column('commission_rate', sa.String(length=50), nullable=True),
    sa.Column('slippage_rate', sa.String(length=50), nullable=True),
    sa.Column('research_rules', sa.JSON(), nullable=True),
    sa.Column('status', sa.Enum('DRAFT', 'PENDING_REVIEW', 'ACCEPTED', 'REJECTED', name='draftstatus'), nullable=False),
    sa.Column('assistant_questions', sa.JSON(), nullable=True),
    sa.Column('assistant_responses', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['draft_id'], ['research_drafts.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_research_draft_revisions_draft_id'), 'research_draft_revisions', ['draft_id'], unique=False)

    op.create_table('research_runs',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('experiment_version_id', sa.UUID(), nullable=False),
    sa.Column('dataset_snapshot_id', sa.UUID(), nullable=True),
    sa.Column('dataset_checksum', sa.String(length=64), nullable=True),
    sa.Column('engine_version', sa.String(length=50), nullable=False),
    sa.Column('config_hash', sa.String(length=64), nullable=False),
    sa.Column('status', sa.Enum('pending', 'running', 'completed', 'failed', name='runstatus'), nullable=True),
    sa.Column('metrics', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('warnings', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('methodology_json', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('error_details', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(), nullable=True),
    sa.Column('completed_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['dataset_snapshot_id'], ['dataset_snapshots.id'], ),
    sa.ForeignKeyConstraint(['experiment_version_id'], ['experiment_versions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('simulation_decisions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('session_id', sa.UUID(), nullable=False),
    sa.Column('step_number', sa.Integer(), nullable=False),
    sa.Column('snapshot_date', sa.String(length=10), nullable=False),
    sa.Column('data_cutoff_at', sa.String(length=10), nullable=False),
    sa.Column('engine_version', sa.String(length=50), nullable=False),
    sa.Column('decision_type', sa.Enum('no_action', 'simulated_entry', 'simulated_exit', 'forced_exit', 'blocked_action', name='decisiontype'), nullable=False),
    sa.Column('decision_detail', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('paper_cash', sa.Float(), nullable=False),
    sa.Column('paper_position', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('paper_equity', sa.Float(), nullable=False),
    sa.Column('warnings', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['session_id'], ['simulation_sessions.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('order_intents',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('user_id', sa.UUID(), nullable=False),
    sa.Column('portfolio_id', sa.UUID(), nullable=False),
    sa.Column('intent_id', sa.String(length=36), nullable=False),
    sa.Column('symbol', sa.String(length=50), nullable=False),
    sa.Column('side', sa.Enum('BUY', 'SELL', name='intentside'), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('order_type', sa.Enum('MARKET', 'LIMIT', name='intentordertype'), nullable=False),
    sa.Column('limit_price', sa.Float(), nullable=True),
    sa.Column('status', sa.Enum('pending', 'authorized', 'submitted', 'filled', 'cancelled', 'rejected', 'uncertain', name='intentstatus'), nullable=False),
    sa.Column('authorization_token', sa.String(length=255), nullable=False),
    sa.Column('expires_at', sa.DateTime(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('rejection_reason', sa.Text(), nullable=True),
    sa.Column('broker_ref', sa.String(length=255), nullable=True),
    sa.Column('idempotency_key', sa.String(length=64), nullable=True),
    sa.Column('fill_source', sa.Enum('simulated', 'broker', name='fillsource'), nullable=True),
    sa.Column('broker_confirmed_at', sa.DateTime(), nullable=True),
    sa.ForeignKeyConstraint(['portfolio_id'], ['paper_portfolios.id'], ),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_order_intents_idempotency_key'), 'order_intents', ['idempotency_key'], unique=True)
    op.create_index(op.f('ix_order_intents_intent_id'), 'order_intents', ['intent_id'], unique=True)

    op.create_table('outcome_evaluations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('decision_id', sa.UUID(), nullable=False),
    sa.Column('evaluated_at', sa.DateTime(), nullable=False),
    sa.Column('actual_outcome_summary', sa.Text(), nullable=True),
    sa.Column('net_simulated_return_pct', sa.Float(), nullable=True),
    sa.Column('max_drawdown_pct', sa.Float(), nullable=True),
    sa.Column('hypothesis_status', sa.Enum('supported', 'partially_supported', 'evidence_insufficient', 'contradicted', name='hypothesisstatus'), nullable=True),
    sa.Column('evaluation_method', sa.String(length=100), nullable=True),
    sa.Column('user_reflection', sa.Text(), nullable=True),
    sa.Column('data_quality_flags', postgresql.JSONB(astext_type=Text()), nullable=True),
    sa.ForeignKeyConstraint(['decision_id'], ['decision_records.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('paper_orders',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('portfolio_id', sa.UUID(), nullable=False),
    sa.Column('decision_id', sa.UUID(), nullable=True),
    sa.Column('asset_id', sa.String(length=50), nullable=False),
    sa.Column('side', sa.Enum('BUY', 'SELL', name='orderside'), nullable=False),
    sa.Column('quantity', sa.Float(), nullable=False),
    sa.Column('order_type', sa.Enum('market', 'limit', name='ordertype'), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('status', sa.Enum('pending', 'filled', 'cancelled', 'rejected', name='orderstatus'), nullable=True),
    sa.Column('model_or_strategy_version_id', sa.UUID(), nullable=True),
    sa.Column('data_cutoff_at', sa.String(length=10), nullable=True),
    sa.Column('rationale', sa.Text(), nullable=True),
    sa.Column('risk_check_status', sa.Enum('passed', 'failed', 'override_requested', name='riskcheckstatus'), nullable=True),
    sa.ForeignKeyConstraint(['decision_id'], ['decision_records.id'], ),
    sa.ForeignKeyConstraint(['portfolio_id'], ['paper_portfolios.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('paper_positions',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('portfolio_id', sa.UUID(), nullable=False),
    sa.Column('asset_id', sa.String(length=50), nullable=False),
    sa.Column('quantity', sa.Float(), nullable=False),
    sa.Column('average_entry_price', sa.Float(), nullable=False),
    sa.Column('opened_at', sa.DateTime(), nullable=False),
    sa.Column('closed_at', sa.DateTime(), nullable=True),
    sa.Column('realized_pnl', sa.Float(), nullable=True),
    sa.Column('unrealized_pnl', sa.Float(), nullable=True),
    sa.Column('status', sa.Enum('open', 'closed', name='positionstatus'), nullable=True),
    sa.ForeignKeyConstraint(['portfolio_id'], ['paper_portfolios.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('fill_reconciliations',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('order_intent_id', sa.UUID(), nullable=False),
    sa.Column('expected_quantity', sa.Integer(), nullable=False),
    sa.Column('filled_quantity', sa.Integer(), nullable=False),
    sa.Column('discrepancy', sa.Boolean(), nullable=False),
    sa.Column('reconciled_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['order_intent_id'], ['order_intents.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('fill_records',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('order_intent_id', sa.UUID(), nullable=False),
    sa.Column('quantity', sa.Integer(), nullable=False),
    sa.Column('price', sa.Float(), nullable=True),
    sa.Column('fill_source', sa.Enum('simulated', 'broker', name='fillsource'), nullable=False),
    sa.Column('broker_ref', sa.String(length=255), nullable=True),
    sa.Column('broker_confirmed_at', sa.DateTime(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.ForeignKeyConstraint(['order_intent_id'], ['order_intents.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('paper_fills',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('paper_order_id', sa.UUID(), nullable=False),
    sa.Column('execution_date', sa.String(length=10), nullable=False),
    sa.Column('observed_price', sa.Float(), nullable=False),
    sa.Column('execution_price_before_costs', sa.Float(), nullable=False),
    sa.Column('slippage_amount', sa.Float(), nullable=False),
    sa.Column('commission_amount', sa.Float(), nullable=False),
    sa.Column('quantity', sa.Float(), nullable=False),
    sa.Column('gross_amount', sa.Float(), nullable=False),
    sa.Column('net_cash_change', sa.Float(), nullable=False),
    sa.Column('simulated_fill_reason', sa.String(length=100), nullable=True),
    sa.ForeignKeyConstraint(['paper_order_id'], ['paper_orders.id'], ),
    sa.PrimaryKeyConstraint('id')
    )

    op.create_table('kill_switches',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('scope', sa.Enum('global', 'training', 'execution', 'inference', name='killswitchscope'), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('activated_at', sa.DateTime(), nullable=True),
    sa.Column('activated_by', sa.String(length=255), nullable=True),
    sa.Column('reason', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('scope')
    )
    op.create_index(op.f('ix_kill_switches_scope'), 'kill_switches', ['scope'], unique=False)
    op.create_index(op.f('ix_kill_switches_is_active'), 'kill_switches', ['is_active'], unique=False)

    op.create_table('rate_limit_counters',
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('identity_key', sa.String(length=255), nullable=False),
    sa.Column('route_category', sa.String(length=100), nullable=False),
    sa.Column('window_start', sa.DateTime(), nullable=False),
    sa.Column('request_count', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('identity_key', 'route_category', 'window_start', name='uq_rate_limit_identity_window')
    )
    op.create_index(op.f('ix_rate_limit_counters_identity_key'), 'rate_limit_counters', ['identity_key'], unique=False)
    op.create_index(op.f('ix_rate_limit_counters_route_category'), 'rate_limit_counters', ['route_category'], unique=False)
    op.create_index(op.f('ix_rate_limit_counters_window_start'), 'rate_limit_counters', ['window_start'], unique=False)


def downgrade() -> None:
    # Drop tables in reverse dependency order.
    # Named circular FK constraints must be dropped before their tables.
    op.drop_index(op.f('ix_rate_limit_counters_window_start'), table_name='rate_limit_counters')
    op.drop_index(op.f('ix_rate_limit_counters_route_category'), table_name='rate_limit_counters')
    op.drop_index(op.f('ix_rate_limit_counters_identity_key'), table_name='rate_limit_counters')
    op.drop_table('rate_limit_counters')
    op.drop_index(op.f('ix_kill_switches_is_active'), table_name='kill_switches')
    op.drop_index(op.f('ix_kill_switches_scope'), table_name='kill_switches')
    op.drop_table('kill_switches')
    op.drop_table('paper_fills')
    op.drop_table('fill_records')
    op.drop_table('fill_reconciliations')
    op.drop_table('paper_positions')
    op.drop_table('paper_orders')
    op.drop_table('outcome_evaluations')
    op.drop_index(op.f('ix_order_intents_intent_id'), table_name='order_intents')
    op.drop_index(op.f('ix_order_intents_idempotency_key'), table_name='order_intents')
    op.drop_table('order_intents')
    op.drop_table('simulation_decisions')
    op.drop_table('research_runs')
    op.drop_index(op.f('ix_research_draft_revisions_draft_id'), table_name='research_draft_revisions')
    op.drop_table('research_draft_revisions')
    op.drop_index(op.f('ix_research_draft_audit_events_user_id'), table_name='research_draft_audit_events')
    op.drop_index(op.f('ix_research_draft_audit_events_draft_id'), table_name='research_draft_audit_events')
    op.drop_table('research_draft_audit_events')
    op.drop_table('paper_portfolios')
    op.drop_table('model_research_exports')
    op.drop_table('model_evaluations')
    op.drop_table('decision_records')
    op.drop_table('training_jobs')
    op.drop_table('simulation_sessions')
    op.drop_table('research_notes')
    op.drop_index(op.f('ix_research_drafts_user_id'), table_name='research_drafts')
    op.drop_index(op.f('ix_research_drafts_experiment_id'), table_name='research_drafts')
    op.drop_table('research_drafts')
    op.drop_index('ix_payment_user_status', table_name='payment_records')
    op.drop_index(op.f('ix_payment_records_user_id'), table_name='payment_records')
    op.drop_table('payment_records')
    op.drop_table('inference_runs')

    # Drop deferred circular FK constraints before dropping their tables
    op.drop_constraint('fk_model_versions_model_card_id', 'model_versions', type_='foreignkey')
    op.drop_constraint('fk_model_cards_model_version_id', 'model_cards', type_='foreignkey')
    op.drop_constraint('fk_experiments_current_version_id', 'experiments', type_='foreignkey')

    op.drop_table('model_versions')
    op.drop_table('model_cards')
    op.drop_table('experiment_versions')
    op.drop_table('experiments')

    op.drop_index(op.f('ix_usage_events_user_id'), table_name='usage_events')
    op.drop_index(op.f('ix_usage_events_event_type'), table_name='usage_events')
    op.drop_table('usage_events')
    op.drop_index(op.f('ix_support_logs_user_id'), table_name='support_logs')
    op.drop_index(op.f('ix_support_logs_recurring_issue_tag'), table_name='support_logs')
    op.drop_index(op.f('ix_support_logs_category'), table_name='support_logs')
    op.drop_table('support_logs')
    op.drop_index(op.f('ix_subscriptions_user_id'), table_name='subscriptions')
    op.drop_table('subscriptions')
    op.drop_index('ix_quota_user_type', table_name='quota_counters')
    op.drop_index(op.f('ix_quota_counters_user_id'), table_name='quota_counters')
    op.drop_table('quota_counters')
    op.drop_index(op.f('ix_pilot_entitlements_user_id'), table_name='pilot_entitlements')
    op.drop_table('pilot_entitlements')
    op.drop_index(op.f('ix_pilot_applications_user_id'), table_name='pilot_applications')
    op.drop_table('pilot_applications')
    op.drop_table('personal_research_memories')
    op.drop_table('model_research_records')
    op.drop_table('execution_agents')
    op.drop_table('dataset_snapshots')
    op.drop_index(op.f('ix_cost_records_owner_id'), table_name='cost_records')
    op.drop_index(op.f('ix_cost_records_cost_type'), table_name='cost_records')
    op.drop_table('cost_records')
    op.drop_index(op.f('ix_audit_events_created_at'), table_name='audit_events')
    op.drop_table('audit_events')
    op.drop_index(op.f('ix_api_keys_user_id'), table_name='api_keys')
    op.drop_index(op.f('ix_api_keys_key_hash'), table_name='api_keys')
    op.drop_table('api_keys')
    op.drop_table('workspaces')
    op.drop_index(op.f('ix_webhook_event_records_event_id'), table_name='webhook_event_records')
    op.drop_table('webhook_event_records')
    op.drop_table('product_hypotheses')
    op.drop_table('plans')
    op.drop_table('datasets')
    op.drop_index(op.f('ix_users_email'), table_name='users')
    op.drop_table('users')

    # Clean up enum types (PostgreSQL enums persist after table drops)
    _drop_enums(op.get_bind())
