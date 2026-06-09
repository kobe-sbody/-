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

-- 将来の成長分析用に拡張しやすいよう、RLSはサービスロール経由のみ想定
alter table public.feedback_history enable row level security;
