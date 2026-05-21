create extension if not exists pgcrypto;

create table if not exists public.scanner_artifacts (
  artifact_key text primary key,
  kind text not null,
  scan_date date,
  updated_at timestamptz not null default now(),
  source_path text,
  payload jsonb not null
);

create table if not exists public.scan_runs (
  run_id text primary key,
  created_at timestamptz not null default now(),
  scan_date date,
  runner text,
  mode text not null,
  status text not null,
  exit_code integer not null default 0,
  etf_signals integer not null default 0,
  stock_live_review integer not null default 0,
  total_scanned integer,
  error_count integer,
  skip_count integer,
  payload jsonb not null default '{}'::jsonb
);

create table if not exists public.signal_events (
  event_key text primary key,
  source text not null check (source in ('etf', 'stock')),
  signal_key text not null,
  scan_date date,
  ticker text,
  name text,
  signal_type text,
  status text,
  active boolean not null default false,
  first_seen_at text,
  last_seen_at text,
  last_checked_at text,
  payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create table if not exists public.live_trades (
  id text primary key,
  status text,
  signal_key text,
  execution_ticker text,
  payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create table if not exists public.execution_map (
  signal_ticker text primary key,
  execution_ticker text,
  payload jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create index if not exists scanner_artifacts_scan_date_idx
  on public.scanner_artifacts (scan_date desc);

create index if not exists scan_runs_created_at_idx
  on public.scan_runs (created_at desc);

create index if not exists signal_events_scan_date_idx
  on public.signal_events (scan_date desc, source, active);

create index if not exists signal_events_ticker_idx
  on public.signal_events (ticker, scan_date desc);

alter table public.scanner_artifacts enable row level security;
alter table public.scan_runs enable row level security;
alter table public.signal_events enable row level security;
alter table public.live_trades enable row level security;
alter table public.execution_map enable row level security;

comment on table public.scanner_artifacts is
  'Latest JSON artifacts for stateless cloud restore and dashboard/API consumption.';
comment on table public.signal_events is
  'Durable ETF and stock signal lifecycle rows: active, faded, close-confirmed, close-failed.';
