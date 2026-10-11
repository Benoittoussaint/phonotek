-- Phonotek : structure de la base Supabase.
-- À coller dans Supabase > SQL Editor > New query, puis « Run ».

-- Profils (Benoit, Eleanor...)
create table if not exists public.profiles (
  id uuid primary key references auth.users on delete cascade,
  name text not null,
  created_at timestamptz not null default now()
);
alter table public.profiles enable row level security;
create policy "profils visibles par les membres" on public.profiles
  for select to authenticated using (true);
create policy "chacun crée son profil" on public.profiles
  for insert to authenticated with check (id = (select auth.uid()));
create policy "chacun modifie son profil" on public.profiles
  for update to authenticated using (id = (select auth.uid()));

-- Disques : CD, vinyles et wishlist de chaque membre.
-- Le détail de chaque disque est rangé dans la colonne « data ».
create table if not exists public.albums (
  id text primary key,
  owner uuid not null default auth.uid() references auth.users on delete cascade,
  data jsonb not null,
  updated_at timestamptz not null default now()
);
create index if not exists albums_owner_idx on public.albums (owner);
alter table public.albums enable row level security;
-- Chacun voit la collection de tout le monde...
create policy "collections visibles par les membres" on public.albums
  for select to authenticated using (true);
-- ... mais ne modifie que la sienne.
create policy "chacun ajoute ses disques" on public.albums
  for insert to authenticated with check (owner = (select auth.uid()));
create policy "chacun modifie ses disques" on public.albums
  for update to authenticated using (owner = (select auth.uid())) with check (owner = (select auth.uid()));
create policy "chacun supprime ses disques" on public.albums
  for delete to authenticated using (owner = (select auth.uid()));

create or replace function public.touch_updated_at() returns trigger
  language plpgsql set search_path = '' as $$
begin
  new.updated_at = now();
  return new;
end $$;
create trigger albums_touch before update on public.albums
  for each row execute function public.touch_updated_at();

-- Pochettes : lisibles par l'application, chacun écrit dans son propre dossier.
insert into storage.buckets (id, name, public) values ('covers', 'covers', true)
  on conflict (id) do nothing;
create policy "pochettes : ajout dans son dossier" on storage.objects
  for insert to authenticated
  with check (bucket_id = 'covers' and (storage.foldername(name))[1] = (select auth.uid())::text);
create policy "pochettes : suppression dans son dossier" on storage.objects
  for delete to authenticated
  using (bucket_id = 'covers' and (storage.foldername(name))[1] = (select auth.uid())::text);
