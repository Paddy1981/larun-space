/**
 * LARUN.SPACE - Authentication System
 * Handles user authentication, subscription management, and session handling
 */

const Auth = {
  // State
  user: null,
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
      targetsPerMonth: -1, // unlimited
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
      price: null, // custom
      targetsPerMonth: -1,
      apiCalls: -1,
      features: ['all', 'whitelabel', 'onpremise', 'custom']
    }
  },

  // Initialize auth
  init() {
    this.loadUser();
    this.updateUI();
  },

  // Load user from localStorage
  loadUser() {
    try {
      const userData = localStorage.getItem('larun_user');
      const token = localStorage.getItem('larun_token');

      if (userData && token) {
        this.user = JSON.parse(userData);
        this.isAuthenticated = true;
      }
    } catch (e) {
      console.error('Failed to load user:', e);
      this.logout();
    }
  },

  // Save user to localStorage
  saveUser() {
    if (this.user) {
      localStorage.setItem('larun_user', JSON.stringify(this.user));
    }
  },

  // ============================================
  // Authentication Methods
  // ============================================

  // Login
  async login(email, password) {
    try {
      // In production, this would call the API
      // const response = await LarunAPI.request('POST', '/auth/login', { email, password });

      // Simulated login for demo
      const user = {
        id: 'user_' + Date.now(),
        email,
        name: email.split('@')[0],
        tier: 'free',
        usage: {
          targetsUsed: 0,
          apiCalls: 0,
          resetDate: new Date(new Date().setMonth(new Date().getMonth() + 1)).toISOString()
        },
        createdAt: new Date().toISOString()
      };

      const token = 'demo_token_' + Date.now();

      this.user = user;
      this.isAuthenticated = true;
      localStorage.setItem('larun_token', token);
      this.saveUser();
      this.updateUI();

      return { success: true, user };
    } catch (error) {
      console.error('Login failed:', error);
      return { success: false, error: error.message };
    }
  },

  // Signup
  async signup(email, password) {
    try {
      // In production, this would call the API
      // const response = await LarunAPI.request('POST', '/auth/signup', { email, password });

      // Simulated signup (same as login for demo)
      return this.login(email, password);
    } catch (error) {
      console.error('Signup failed:', error);
      return { success: false, error: error.message };
    }
  },

  // Logout
  logout() {
    this.user = null;
    this.isAuthenticated = false;
    localStorage.removeItem('larun_user');
    localStorage.removeItem('larun_token');
    this.updateUI();
  },

  // ============================================
  // Subscription Management
  // ============================================

  // Get current tier info
  getCurrentTier() {
    const tierName = this.user?.tier || 'free';
    return {
      name: tierName,
      ...this.tiers[tierName]
    };
  },

  // Check if user can use a feature
  canUseFeature(feature) {
    const tier = this.getCurrentTier();

    // Enterprise has all features
    if (tier.features.includes('all')) return true;

    return tier.features.includes(feature);
  },

  // Check if user has remaining usage
  hasRemainingUsage(type = 'targets') {
    const tier = this.getCurrentTier();
    const usage = this.user?.usage || { targetsUsed: 0, apiCalls: 0 };

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

  // Increment usage
  incrementUsage(type = 'targets') {
    if (!this.user?.usage) return;

    if (type === 'targets') {
      this.user.usage.targetsUsed++;
    } else if (type === 'api') {
      this.user.usage.apiCalls++;
    }

    this.saveUser();
    this.updateUI();
  },

  // Get usage stats
  getUsageStats() {
    const tier = this.getCurrentTier();
    const usage = this.user?.usage || { targetsUsed: 0, apiCalls: 0 };

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
      },
      resetDate: usage.resetDate
    };
  },

  // ============================================
  // UI Updates
  // ============================================

  updateUI() {
    // Update user avatar and info in sidebar
    const avatar = document.getElementById('user-avatar');
    const userName = document.getElementById('user-name');
    const userTier = document.getElementById('user-tier');

    if (avatar) {
      avatar.textContent = this.user?.name?.[0]?.toUpperCase() || '?';
    }

    if (userName) {
      userName.textContent = this.user?.name || 'Guest';
    }

    if (userTier) {
      const tier = this.getCurrentTier();
      userTier.textContent = `${tier.name}${tier.price ? '' : ' (Free)'}`;
    }

    // Update any upgrade buttons or feature gates
    this.updateFeatureGates();
  },

  updateFeatureGates() {
    // Hide/show elements based on tier
    document.querySelectorAll('[data-requires-tier]').forEach(el => {
      const requiredTier = el.dataset.requiresTier;
      const hasAccess = this.canUseFeature(requiredTier);
      el.style.display = hasAccess ? '' : 'none';
    });

    document.querySelectorAll('[data-requires-auth]').forEach(el => {
      el.style.display = this.isAuthenticated ? '' : 'none';
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
  document.getElementById('auth-email').value = '';
  document.getElementById('auth-password').value = '';
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
      closeAuthModal();
      // Redirect to app if on landing page
      if (window.location.pathname.endsWith('index.html') || window.location.pathname === '/') {
        window.location.href = 'app.html';
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

function toggleUserMenu() {
  // In a full implementation, this would show a dropdown with options
  if (Auth.isAuthenticated) {
    if (confirm('Do you want to log out?')) {
      Auth.logout();
    }
  } else {
    openAuthModal('login');
  }
}

function openSettings() {
  // Placeholder for settings modal
  alert('Settings panel coming soon!');
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
