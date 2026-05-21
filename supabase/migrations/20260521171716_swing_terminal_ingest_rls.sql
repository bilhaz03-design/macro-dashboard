create extension if not exists pgcrypto;

create schema if not exists private;
revoke all on schema private from public;
revoke all on schema private from anon;
revoke all on schema private from authenticated;

create table if not exists private.scanner_ingest_keys (
  key_id text primary key,
  token_sha256 text not null,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  rotated_at timestamptz
);

revoke all on table private.scanner_ingest_keys from public;
revoke all on table private.scanner_ingest_keys from anon;
revoke all on table private.scanner_ingest_keys from authenticated;

create or replace function public.scanner_ingest_authorized()
returns boolean
language plpgsql
security definer
set search_path = public, private
as $$
declare
  headers json;
  token text;
  token_hash text;
begin
  headers := coalesce(nullif(current_setting('request.headers', true), ''), '{}')::json;
  token := nullif(headers ->> 'x-swing-terminal-token', '');
  if token is null then
    return false;
  end if;

  token_hash := encode(extensions.digest(token, 'sha256'), 'hex');
  return exists (
    select 1
    from private.scanner_ingest_keys
    where active is true
      and token_sha256 = token_hash
  );
exception when others then
  return false;
end;
$$;

revoke all on function public.scanner_ingest_authorized() from public;
grant execute on function public.scanner_ingest_authorized() to anon, authenticated, service_role;

grant usage on schema public to anon, authenticated;
grant select, insert, update on public.scanner_artifacts to anon, authenticated;
grant select, insert, update on public.scan_runs to anon, authenticated;
grant select, insert, update on public.signal_events to anon, authenticated;
grant select, insert, update on public.live_trades to anon, authenticated;
grant select, insert, update on public.execution_map to anon, authenticated;

drop policy if exists scanner_artifacts_ingest_select on public.scanner_artifacts;
drop policy if exists scanner_artifacts_ingest_insert on public.scanner_artifacts;
drop policy if exists scanner_artifacts_ingest_update on public.scanner_artifacts;
create policy scanner_artifacts_ingest_select on public.scanner_artifacts
  for select to anon, authenticated using (public.scanner_ingest_authorized());
create policy scanner_artifacts_ingest_insert on public.scanner_artifacts
  for insert to anon, authenticated with check (public.scanner_ingest_authorized());
create policy scanner_artifacts_ingest_update on public.scanner_artifacts
  for update to anon, authenticated using (public.scanner_ingest_authorized()) with check (public.scanner_ingest_authorized());

drop policy if exists scan_runs_ingest_select on public.scan_runs;
drop policy if exists scan_runs_ingest_insert on public.scan_runs;
drop policy if exists scan_runs_ingest_update on public.scan_runs;
create policy scan_runs_ingest_select on public.scan_runs
  for select to anon, authenticated using (public.scanner_ingest_authorized());
create policy scan_runs_ingest_insert on public.scan_runs
  for insert to anon, authenticated with check (public.scanner_ingest_authorized());
create policy scan_runs_ingest_update on public.scan_runs
  for update to anon, authenticated using (public.scanner_ingest_authorized()) with check (public.scanner_ingest_authorized());

drop policy if exists signal_events_ingest_select on public.signal_events;
drop policy if exists signal_events_ingest_insert on public.signal_events;
drop policy if exists signal_events_ingest_update on public.signal_events;
create policy signal_events_ingest_select on public.signal_events
  for select to anon, authenticated using (public.scanner_ingest_authorized());
create policy signal_events_ingest_insert on public.signal_events
  for insert to anon, authenticated with check (public.scanner_ingest_authorized());
create policy signal_events_ingest_update on public.signal_events
  for update to anon, authenticated using (public.scanner_ingest_authorized()) with check (public.scanner_ingest_authorized());

drop policy if exists live_trades_ingest_select on public.live_trades;
drop policy if exists live_trades_ingest_insert on public.live_trades;
drop policy if exists live_trades_ingest_update on public.live_trades;
create policy live_trades_ingest_select on public.live_trades
  for select to anon, authenticated using (public.scanner_ingest_authorized());
create policy live_trades_ingest_insert on public.live_trades
  for insert to anon, authenticated with check (public.scanner_ingest_authorized());
create policy live_trades_ingest_update on public.live_trades
  for update to anon, authenticated using (public.scanner_ingest_authorized()) with check (public.scanner_ingest_authorized());

drop policy if exists execution_map_ingest_select on public.execution_map;
drop policy if exists execution_map_ingest_insert on public.execution_map;
drop policy if exists execution_map_ingest_update on public.execution_map;
create policy execution_map_ingest_select on public.execution_map
  for select to anon, authenticated using (public.scanner_ingest_authorized());
create policy execution_map_ingest_insert on public.execution_map
  for insert to anon, authenticated with check (public.scanner_ingest_authorized());
create policy execution_map_ingest_update on public.execution_map
  for update to anon, authenticated using (public.scanner_ingest_authorized()) with check (public.scanner_ingest_authorized());

comment on function public.scanner_ingest_authorized() is
  'Authorizes scanner REST reads/writes with x-swing-terminal-token. Token hashes live in private.scanner_ingest_keys.';
