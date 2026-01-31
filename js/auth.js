/**
 * LARUN.SPACE - Authentication System with Supabase
 * Handles user authentication, API keys, and subscription management
 */

// Supabase Configuration
const SUPABASE_URL = 'https://mwmbcfcvnkwegrjlauis.supabase.co';
const SUPABASE_ANON_KEY = 'sb_publishable_0qRtiBlacrDCoUrQkNXnoQ_TCmZwk3k';

// Initialize Supabase client
let supabase;
if (window.supabase) {
  supabase = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
}

const Auth = {
  // State
  user: null,
  session: null,
  isAuthenticated: false,

  // Subscription tiers and limits
  tiers: {
    free: {
      name: 'Explorer',
      targetsPerMonth: 10,
      apiCalls: 0,
      features: ['tinyml', 'tess_basic', 'cli_basic']
    },
    researcher: {
      name: 'Researcher',
      price: 9,
      targetsPerMonth: -1,
      apiCalls: 1000,
      features: ['tinyml', 'tess', 'bls', 'fit', 'cli', 'api', 'reports']
    },
    scientist: {
      name: 'Scientist',
      price: 29,
      targetsPerMonth: -1,
      apiCalls: -1,
      features: ['tinyml', 'tess', 'kepler', 'jwst', 'bls', 'fit', 'ttv', 'cli', 'api', 'reports', 'priority']
    },
    enterprise: {
      name: 'Enterprise',
      price: null,
      targetsPerMonth: -1,
      apiCalls: -1,
      features: ['all', 'whitelabel', 'onpremise', 'custom']
    }
  },

  // Initialize auth
  async init() {
    if (!supabase) {
      console.warn('Supabase not loaded');
      return;
    }

    // Check for existing session
    const { data: { session } } = await supabase.auth.getSession();
    if (session) {
      this.session = session;
      this.user = session.user;
      this.isAuthenticated = true;
      await this.loadUserProfile();
    }

    // Listen for auth changes
    supabase.auth.onAuthStateChange(async (event, session) => {
      console.log('Auth event:', event);
      if (session) {
        this.session = session;
        this.user = session.user;
        this.isAuthenticated = true;
        await this.loadUserProfile();
      } else {
        this.session = null;
        this.user = null;
        this.isAuthenticated = false;
      }
      this.updateUI();
    });

    this.updateUI();
  },

  // Load user profile from database
  async loadUserProfile() {
    if (!this.user) return;

    try {
      const { data, error } = await supabase
        .from('profiles')
        .select('*')
        .eq('id', this.user.id)
        .single();

      if (data) {
        this.user.profile = data;
      }
    } catch (e) {
      console.log('Profile not found, using defaults');
    }
  },

  // ============================================
  // Authentication Methods
  // ============================================

  // Login with email
  async login(email, password) {
    try {
      const { data, error } = await supabase.auth.signInWithPassword({
        email,
        password,
      });

      if (error) throw error;

      this.session = data.session;
      this.user = data.user;
      this.isAuthenticated = true;
      await this.loadUserProfile();
      this.updateUI();

      return { success: true, user: data.user };
    } catch (error) {
      console.error('Login failed:', error);
      return { success: false, error: error.message };
    }
  },

  // Signup with email
  async signup(email, password) {
    try {
      const { data, error } = await supabase.auth.signUp({
        email,
        password,
      });

      if (error) throw error;

      return {
        success: true,
        user: data.user,
        message: data.user?.identities?.length === 0
          ? 'Account already exists. Please sign in.'
          : 'Check your email to confirm your account!'
      };
    } catch (error) {
      console.error('Signup failed:', error);
      return { success: false, error: error.message };
    }
  },

  // Login with GitHub
  async loginWithGitHub() {
    try {
      const { data, error } = await supabase.auth.signInWithOAuth({
        provider: 'github',
        options: {
          redirectTo: window.location.origin + '/app.html'
        }
      });

      if (error) throw error;
      return { success: true };
    } catch (error) {
      console.error('GitHub login failed:', error);
      return { success: false, error: error.message };
    }
  },

  // Logout
  async logout() {
    try {
      await supabase.auth.signOut();
      this.user = null;
      this.session = null;
      this.isAuthenticated = false;
      this.updateUI();
    } catch (error) {
      console.error('Logout failed:', error);
    }
  },

  // ============================================
  // API Key Management
  // ============================================

  // Generate new API key
  async generateApiKey(name = 'Default Key') {
    if (!this.user) throw new Error('Must be logged in');

    // Generate secure random key
    const keyBytes = new Uint8Array(24);
    crypto.getRandomValues(keyBytes);
    const apiKey = 'larun_' + Array.from(keyBytes).map(b => b.toString(16).padStart(2, '0')).join('');

    // Hash the key for storage
    const keyHash = await this.hashString(apiKey);

    // Store in database (only the hash)
    const { data, error } = await supabase
      .from('api_keys')
      .insert({
        user_id: this.user.id,
        key_hash: keyHash,
        key_prefix: apiKey.substring(0, 14) + '...',
        name: name
      })
      .select()
      .single();

    if (error) throw error;

    // Return full key (only shown once!)
    return {
      id: data.id,
      name: data.name,
      key_prefix: data.key_prefix,
      full_key: apiKey,
      created_at: data.created_at
    };
  },

  // List user's API keys
  async listApiKeys() {
    if (!this.user) return [];

    const { data, error } = await supabase
      .from('api_keys')
      .select('id, name, key_prefix, created_at, last_used_at')
      .eq('user_id', this.user.id)
      .order('created_at', { ascending: false });

    if (error) {
      console.error('Failed to list API keys:', error);
      return [];
    }
    return data || [];
  },

  // Revoke an API key
  async revokeApiKey(keyId) {
    if (!this.user) throw new Error('Must be logged in');

    const { error } = await supabase
      .from('api_keys')
      .delete()
      .eq('id', keyId)
      .eq('user_id', this.user.id);

    if (error) throw error;
    return true;
  },

  // Hash string using SHA-256
  async hashString(str) {
    const encoder = new TextEncoder();
    const data = encoder.encode(str);
    const hashBuffer = await crypto.subtle.digest('SHA-256', data);
    const hashArray = Array.from(new Uint8Array(hashBuffer));
    return hashArray.map(b => b.toString(16).padStart(2, '0')).join('');
  },

  // ============================================
  // Subscription Management
  // ============================================

  getCurrentTier() {
    const tierName = this.user?.profile?.tier || 'free';
    return {
      name: tierName,
      ...this.tiers[tierName]
    };
  },

  canUseFeature(feature) {
    const tier = this.getCurrentTier();
    if (tier.features.includes('all')) return true;
    return tier.features.includes(feature);
  },

  hasRemainingUsage(type = 'targets') {
    const tier = this.getCurrentTier();
    const usage = this.user?.profile?.usage || { targetsUsed: 0, apiCalls: 0 };

    if (type === 'targets') {
      if (tier.targetsPerMonth === -1) return true;
      return usage.targetsUsed < tier.targetsPerMonth;
    }

    if (type === 'api') {
      if (tier.apiCalls === -1) return true;
      if (tier.apiCalls === 0) return false;
      return usage.apiCalls < tier.apiCalls;
    }

    return false;
  },

  getUsageStats() {
    const tier = this.getCurrentTier();
    const usage = this.user?.profile?.usage || { targetsUsed: 0, apiCalls: 0 };

    return {
      targets: {
        used: usage.targetsUsed,
        limit: tier.targetsPerMonth,
        remaining: tier.targetsPerMonth === -1 ? 'Unlimited' : tier.targetsPerMonth - usage.targetsUsed
      },
      api: {
        used: usage.apiCalls,
        limit: tier.apiCalls,
        remaining: tier.apiCalls === -1 ? 'Unlimited' : tier.apiCalls - usage.apiCalls
      }
    };
  },

  // ============================================
  // UI Updates
  // ============================================

  updateUI() {
    const avatar = document.getElementById('user-avatar');
    const userName = document.getElementById('user-name');
    const userTier = document.getElementById('user-tier');

    if (this.isAuthenticated && this.user) {
      const displayName = this.user.user_metadata?.user_name ||
                          this.user.user_metadata?.name ||
                          this.user.email?.split('@')[0] ||
                          'User';

      if (avatar) {
        avatar.textContent = displayName[0]?.toUpperCase() || '?';
      }
      if (userName) {
        userName.textContent = displayName;
      }
      if (userTier) {
        const tier = this.getCurrentTier();
        userTier.textContent = tier.name;
      }

      // Update nav for logged in state
      const signInBtn = document.querySelector('.btn-ghost[onclick*="login"]');
      if (signInBtn) {
        signInBtn.textContent = displayName;
        signInBtn.onclick = () => toggleUserMenu();
      }
    } else {
      if (avatar) avatar.textContent = '?';
      if (userName) userName.textContent = 'Guest';
      if (userTier) userTier.textContent = 'Free';
    }

    this.updateFeatureGates();
  },

  updateFeatureGates() {
    document.querySelectorAll('[data-requires-tier]').forEach(el => {
      const requiredTier = el.dataset.requiresTier;
      const hasAccess = this.canUseFeature(requiredTier);
      el.style.display = hasAccess ? '' : 'none';
    });

    document.querySelectorAll('[data-requires-auth]').forEach(el => {
      el.style.display = this.isAuthenticated ? '' : 'none';
    });

    document.querySelectorAll('[data-hide-if-auth]').forEach(el => {
      el.style.display = this.isAuthenticated ? 'none' : '';
    });
  }
};

// ============================================
// Modal Management
// ============================================

let authMode = 'login';

function openAuthModal(mode = 'login') {
  authMode = mode;
  updateAuthModal();
  document.getElementById('auth-modal')?.classList.add('active');
}

function closeAuthModal() {
  document.getElementById('auth-modal')?.classList.remove('active');
  const emailInput = document.getElementById('auth-email');
  const passwordInput = document.getElementById('auth-password');
  if (emailInput) emailInput.value = '';
  if (passwordInput) passwordInput.value = '';
}

function toggleAuthMode() {
  authMode = authMode === 'login' ? 'signup' : 'login';
  updateAuthModal();
}

function updateAuthModal() {
  const title = document.getElementById('auth-modal-title');
  const submitText = document.getElementById('auth-submit-text');
  const toggleText = document.getElementById('auth-toggle-text');
  const toggleLink = document.getElementById('auth-toggle-link');

  if (authMode === 'login') {
    if (title) title.textContent = 'Sign In';
    if (submitText) submitText.textContent = 'Sign In';
    if (toggleText) toggleText.textContent = "Don't have an account?";
    if (toggleLink) toggleLink.textContent = 'Sign Up';
  } else {
    if (title) title.textContent = 'Create Account';
    if (submitText) submitText.textContent = 'Create Account';
    if (toggleText) toggleText.textContent = 'Already have an account?';
    if (toggleLink) toggleLink.textContent = 'Sign In';
  }
}

async function handleAuth(event) {
  event.preventDefault();

  const email = document.getElementById('auth-email')?.value;
  const password = document.getElementById('auth-password')?.value;

  if (!email || !password) return;

  const submitBtn = event.target.querySelector('button[type="submit"]');
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.innerHTML = '<span>Please wait...</span>';
  }

  try {
    const result = authMode === 'login'
      ? await Auth.login(email, password)
      : await Auth.signup(email, password);

    if (result.success) {
      if (result.message) {
        alert(result.message);
      }
      closeAuthModal();
      if (authMode === 'login') {
        if (window.location.pathname.endsWith('index.html') || window.location.pathname === '/') {
          window.location.href = 'app.html';
        }
      }
    } else {
      alert(result.error || 'Authentication failed. Please try again.');
    }
  } catch (error) {
    alert('An error occurred. Please try again.');
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.innerHTML = `<span>${authMode === 'login' ? 'Sign In' : 'Create Account'}</span>`;
    }
  }
}

async function handleGitHubLogin() {
  const result = await Auth.loginWithGitHub();
  if (!result.success) {
    alert(result.error || 'GitHub login failed. Please try again.');
  }
}

function toggleUserMenu() {
  if (Auth.isAuthenticated) {
    const menu = document.getElementById('user-menu');
    if (menu) {
      menu.classList.toggle('active');
    } else {
      if (confirm('Do you want to log out?')) {
        Auth.logout();
      }
    }
  } else {
    openAuthModal('login');
  }
}

function openSettings() {
  window.location.href = 'app.html#settings';
}

// Close modal on overlay click
document.addEventListener('click', (event) => {
  if (event.target.classList.contains('modal-overlay')) {
    event.target.classList.remove('active');
  }
});

// Close modal on Escape key
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    document.querySelectorAll('.modal-overlay.active').forEach(modal => {
      modal.classList.remove('active');
    });
  }
});

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => {
  Auth.init();
});
