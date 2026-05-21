create extension if not exists pg_cron with schema pg_catalog;
create extension if not exists pg_net;

create schema if not exists private;

create or replace function private.dispatch_swing_terminal_workflow(
  workflow_name text,
  payload jsonb
)
returns bigint
language plpgsql
security definer
set search_path = private, vault, public
as $$
declare
  github_token text;
  dispatch_inputs jsonb;
  force_input text;
  mode_input text;
  request_id bigint;
begin
  if workflow_name not in (
    'swing-terminal-cloud.yml',
    'swing-terminal-watchdog.yml'
  ) then
    raise warning 'Swing Terminal workflow is not allowed: %', workflow_name;
    return null;
  end if;

  force_input := case
    when coalesce(payload ->> 'force', 'false') = 'true' then 'true'
    else 'false'
  end;

  if workflow_name = 'swing-terminal-watchdog.yml' then
    dispatch_inputs := jsonb_build_object('force', force_input);
  else
    mode_input := coalesce(payload ->> 'mode', 'etf');
    if mode_input not in ('etf', 'stocks', 'all') then
      raise warning 'Swing Terminal scan mode is not allowed: %', mode_input;
      return null;
    end if;
    dispatch_inputs := jsonb_build_object(
      'mode', mode_input,
      'force', force_input
    );
  end if;

  select decrypted_secret
    into github_token
  from vault.decrypted_secrets
  where name = 'swing_terminal_github_actions_token'
  order by created_at desc
  limit 1;

  if github_token is null or length(github_token) < 20 then
    raise warning 'Missing Vault secret: swing_terminal_github_actions_token';
    return null;
  end if;

  select net.http_post(
    url := format(
      'https://api.github.com/repos/%s/actions/workflows/%s/dispatches',
      'bilhaz03-design/macro-dashboard',
      workflow_name
    ),
    headers := jsonb_build_object(
      'Content-Type', 'application/json',
      'Accept', 'application/vnd.github+json',
      'Authorization', 'Bearer ' || github_token,
      'User-Agent', 'swing-terminal-supabase-cron',
      'X-GitHub-Api-Version', '2022-11-28'
    ),
    body := jsonb_build_object(
      'ref', 'main',
      'inputs', dispatch_inputs
    ),
    timeout_milliseconds := 10000
  )
    into request_id;

  return request_id;
end;
$$;

revoke all on function private.dispatch_swing_terminal_workflow(text, jsonb) from public;
revoke all on function private.dispatch_swing_terminal_workflow(text, jsonb) from anon;
revoke all on function private.dispatch_swing_terminal_workflow(text, jsonb) from authenticated;

do $$
declare
  existing_job_id bigint;
begin
  for existing_job_id in
    select jobid
    from cron.job
    where jobname in (
      'swing-terminal-master-etf-15m',
      'swing-terminal-master-stocks-hourly',
      'swing-terminal-master-watchdog-30m'
    )
  loop
    perform cron.unschedule(existing_job_id);
  end loop;
end $$;

select cron.schedule(
  'swing-terminal-master-etf-15m',
  '7,22,37,52 7-21 * * 1-5',
  $$
    select private.dispatch_swing_terminal_workflow(
      'swing-terminal-cloud.yml',
      '{"mode":"etf","force":"false"}'::jsonb
    );
  $$
);

select cron.schedule(
  'swing-terminal-master-stocks-hourly',
  '11 7-21 * * 1-5',
  $$
    select private.dispatch_swing_terminal_workflow(
      'swing-terminal-cloud.yml',
      '{"mode":"stocks","force":"false"}'::jsonb
    );
  $$
);

select cron.schedule(
  'swing-terminal-master-watchdog-30m',
  '13,43 7-21 * * 1-5',
  $$
    select private.dispatch_swing_terminal_workflow(
      'swing-terminal-watchdog.yml',
      '{"force":"false"}'::jsonb
    );
  $$
);
