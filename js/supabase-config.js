// Supabase Configuration for LARUN.SPACE
const SUPABASE_URL = 'https://mwmbcfcvnkwegrjlauis.supabase.co';
const SUPABASE_ANON_KEY = 'sb_publishable_0qRtiBlacrDCoUrQkNXnoQ_TCmZwk3k';

// Initialize Supabase client
const supabase = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);

// Auth state management
let currentUser = null;

// Check auth state on load
async function checkAuth() {
  const { data: { session } } = await supabase.auth.getSession();
  if (session) {
    currentUser = session.user;
    updateUIForLoggedInUser(session.user);
  }
  return session;
}

// Sign up with email
async function signUpWithEmail(email, password) {
  const { data, error } = await supabase.auth.signUp({
    email,
    password,
  });
  if (error) throw error;
  return data;
}

// Sign in with email
async function signInWithEmail(email, password) {
  const { data, error } = await supabase.auth.signInWithPassword({
    email,
    password,
  });
  if (error) throw error;
  currentUser = data.user;
  updateUIForLoggedInUser(data.user);
  return data;
}

// Sign in with GitHub
async function signInWithGitHub() {
  const { data, error } = await supabase.auth.signInWithOAuth({
    provider: 'github',
    options: {
      redirectTo: window.location.origin + '/app.html'
    }
  });
  if (error) throw error;
  return data;
}

// Sign out
async function signOut() {
  const { error } = await supabase.auth.signOut();
  if (error) throw error;
  currentUser = null;
  updateUIForLoggedOutUser();
}

// Generate API key for user
async function generateApiKey(name = 'default') {
  if (!currentUser) throw new Error('Must be logged in');

  // Generate a secure random key
  const keyBytes = new Uint8Array(32);
  crypto.getRandomValues(keyBytes);
  const apiKey = 'larun_' + Array.from(keyBytes).map(b => b.toString(16).padStart(2, '0')).join('');

  // Store in database
  const { data, error } = await supabase
    .from('api_keys')
    .insert({
      user_id: currentUser.id,
      key_hash: await hashApiKey(apiKey),
      key_prefix: apiKey.substring(0, 12),
      name: name,
      created_at: new Date().toISOString()
    })
    .select()
    .single();

  if (error) throw error;

  // Return the full key (only shown once)
  return { ...data, full_key: apiKey };
}

// List user's API keys
async function listApiKeys() {
  if (!currentUser) throw new Error('Must be logged in');

  const { data, error } = await supabase
    .from('api_keys')
    .select('*')
    .eq('user_id', currentUser.id)
    .order('created_at', { ascending: false });

  if (error) throw error;
  return data;
}

// Revoke an API key
async function revokeApiKey(keyId) {
  if (!currentUser) throw new Error('Must be logged in');

  const { error } = await supabase
    .from('api_keys')
    .delete()
    .eq('id', keyId)
    .eq('user_id', currentUser.id);

  if (error) throw error;
}

// Hash API key for storage
async function hashApiKey(key) {
  const encoder = new TextEncoder();
  const data = encoder.encode(key);
  const hashBuffer = await crypto.subtle.digest('SHA-256', data);
  const hashArray = Array.from(new Uint8Array(hashBuffer));
  return hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
}

// Get user's subscription tier
async function getUserTier() {
  if (!currentUser) return 'free';

  const { data, error } = await supabase
    .from('subscriptions')
    .select('tier')
    .eq('user_id', currentUser.id)
    .single();

  if (error || !data) return 'free';
  return data.tier;
}

// Update UI for logged in user
function updateUIForLoggedInUser(user) {
  // Update nav buttons
  const signInBtn = document.querySelector('.btn-ghost[onclick*="login"]');
  const getStartedBtn = document.querySelector('.nav-actions .btn-primary');

  if (signInBtn) {
    signInBtn.textContent = user.email || user.user_metadata?.user_name || 'Account';
    signInBtn.onclick = () => window.location.href = 'app.html';
  }

  if (getStartedBtn) {
    getStartedBtn.textContent = 'Dashboard';
    getStartedBtn.href = 'app.html';
  }
}

// Update UI for logged out user
function updateUIForLoggedOutUser() {
  const signInBtn = document.querySelector('.btn-ghost');
  const getStartedBtn = document.querySelector('.nav-actions .btn-primary');

  if (signInBtn) {
    signInBtn.textContent = 'Sign In';
    signInBtn.onclick = () => openAuthModal('login');
  }

  if (getStartedBtn) {
    getStartedBtn.textContent = 'Get Started';
    getStartedBtn.href = 'app.html';
  }
}

// Initialize on page load
document.addEventListener('DOMContentLoaded', checkAuth);
