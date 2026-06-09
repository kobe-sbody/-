-- Studio Coach: 添削履歴テーブル
-- Supabase SQL Editor で実行してください

create table if not exists public.feedback_history (
  id uuid primary key default gen_random_uuid(),
  created_at timestamptz not null default now(),
  staff_name text not null,
  audio_file_name text not null default '',
  transcript text not null default '',
  feedback text not null default ''
);

create index if not exists feedback_history_created_at_idx
  on public.feedback_history (created_at desc);

create index if not exists feedback_history_staff_name_idx
  on public.feedback_history (staff_name);

-- サーバー（service_role）からのみアクセスする想定。
-- RLS を有効にすると anon キーでは保存・取得できないため、初期設定では無効化。
alter table public.feedback_history disable row level security;
