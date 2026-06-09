-- 既に feedback_history.sql を実行済みで履歴が保存されない場合に実行
-- 原因: RLS 有効 + ポリシーなし → anon キーでは insert/select が拒否される

alter table public.feedback_history disable row level security;
