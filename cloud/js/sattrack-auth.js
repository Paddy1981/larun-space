/**
 * sattrack-auth.js — Supabase auth for sattrack.larun.space
 *
 * Uses window.supabase global loaded from CDN (loaded before this module).
 * storageKey 'sattrack-auth' avoids colliding with larun.space's 'larun-auth' session.
 */

const SUPABASE_URL = 'https://mwmbcfcvnkwegrjlauis.supabase.co';
const SUPABASE_ANON_KEY = 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im13bWJjZmN2bmt3ZWdyamxhdWlzIiwicm9sZSI6ImFub24iLCJpYXQiOjE3Njk4NjE5OTEsImV4cCI6MjA4NTQzNzk5MX0.3g5VZ4aL_tvkztXlHxiY0-rec5D9QwnST-m9l54NVPk';

const client = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY, {
  auth: { storageKey: 'sattrack-auth' },
});

export async function getSession() {
  const { data: { session } } = await client.auth.getSession();
  return session;
}

export async function signInWithGitHub() {
  const { data, error } = await client.auth.signInWithOAuth({
    provider: 'github',
    options: { redirectTo: window.location.origin + '/' },
  });
  if (error) throw error;
  return data;
}

export async function signInWithGoogle() {
  const { data, error } = await client.auth.signInWithOAuth({
    provider: 'google',
    options: { redirectTo: window.location.origin + '/' },
  });
  if (error) throw error;
  return data;
}

export async function signOut() {
  const { error } = await client.auth.signOut();
  if (error) throw error;
}

export function onAuthStateChange(callback) {
  client.auth.onAuthStateChange((_event, session) => {
    callback(session);
  });
}
