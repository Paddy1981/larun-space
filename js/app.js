/**
 * LARUN.SPACE - Main Application
 * Ties together all modules and handles app-level functionality
 */

const App = {
  // Configuration
  config: {
    apiBaseURL: 'http://localhost:8000',
    version: '1.0.0'
  },

  // Initialize the application
  init() {
    console.log('LARUN.SPACE v' + this.config.version);

    // Initialize modules
    this.initModules();

    // Setup global event listeners
    this.setupEventListeners();

    // Check for URL parameters
    this.handleURLParams();

    // Load any pending state
    this.loadState();
  },

  // Initialize all modules
  initModules() {
    // Auth is auto-initialized
    // Chat is auto-initialized

    // Update API base URL if configured
    if (typeof LarunAPI !== 'undefined') {
      LarunAPI.baseURL = this.config.apiBaseURL;
    }
  },

  // Setup global event listeners
  setupEventListeners() {
    // Handle beforeunload
    window.addEventListener('beforeunload', () => {
      this.saveState();
    });

    // Handle visibility change (tab focus)
    document.addEventListener('visibilitychange', () => {
      if (document.visibilityState === 'visible') {
        this.onResume();
      }
    });

    // Handle keyboard shortcuts
    document.addEventListener('keydown', (event) => {
      this.handleKeyboardShortcut(event);
    });

    // Handle network status
    window.addEventListener('online', () => {
      this.showToast('Connection restored', 'success');
    });

    window.addEventListener('offline', () => {
      this.showToast('You are offline. Some features may be limited.', 'warning');
    });
  },

  // Handle URL parameters
  handleURLParams() {
    const params = new URLSearchParams(window.location.search);

    // Handle TIC parameter for direct target lookup
    const tic = params.get('tic');
    if (tic) {
      setTimeout(() => {
        useSuggestedPrompt(`Search for transits in TIC ${tic}`);
      }, 500);
    }

    // Handle action parameter
    const action = params.get('action');
    if (action === 'login') {
      openAuthModal('login');
    } else if (action === 'signup') {
      openAuthModal('signup');
    }
  },

  // Handle keyboard shortcuts
  handleKeyboardShortcut(event) {
    // Cmd/Ctrl + K: Focus search/input
    if ((event.metaKey || event.ctrlKey) && event.key === 'k') {
      event.preventDefault();
      const input = document.getElementById('message-input');
      if (input) input.focus();
    }

    // Cmd/Ctrl + N: New chat
    if ((event.metaKey || event.ctrlKey) && event.key === 'n') {
      event.preventDefault();
      startNewChat();
    }

    // Cmd/Ctrl + /: Toggle sidebar
    if ((event.metaKey || event.ctrlKey) && event.key === '/') {
      event.preventDefault();
      toggleSidebar();
    }
  },

  // Save application state
  saveState() {
    const state = {
      timestamp: Date.now(),
      currentConversationId: Chat.currentConversation?.id
    };

    try {
      localStorage.setItem('larun_app_state', JSON.stringify(state));
    } catch (e) {
      console.error('Failed to save state:', e);
    }
  },

  // Load application state
  loadState() {
    try {
      const stateStr = localStorage.getItem('larun_app_state');
      if (!stateStr) return;

      const state = JSON.parse(stateStr);

      // Restore conversation if recent
      if (state.currentConversationId && Date.now() - state.timestamp < 3600000) {
        Chat.selectConversation(state.currentConversationId);
      }
    } catch (e) {
      console.error('Failed to load state:', e);
    }
  },

  // Called when app resumes (tab focused)
  onResume() {
    // Could refresh data, check for updates, etc.
  },

  // Show toast notification
  showToast(message, type = 'info') {
    // Create toast element
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.style.cssText = `
      position: fixed;
      bottom: 20px;
      left: 50%;
      transform: translateX(-50%);
      background: var(--space-dark);
      border: 1px solid var(--border-gray);
      border-radius: var(--radius-lg);
      padding: var(--space-md) var(--space-lg);
      color: var(--pure-white);
      font-size: 0.875rem;
      z-index: 3000;
      animation: toastIn 0.3s ease;
    `;

    // Set border color based on type
    if (type === 'success') toast.style.borderColor = 'var(--success)';
    if (type === 'warning') toast.style.borderColor = 'var(--warning)';
    if (type === 'error') toast.style.borderColor = 'var(--error)';

    toast.textContent = message;
    document.body.appendChild(toast);

    // Remove after delay
    setTimeout(() => {
      toast.style.animation = 'toastOut 0.3s ease';
      setTimeout(() => toast.remove(), 300);
    }, 3000);
  },

  // Feature flag check
  isFeatureEnabled(feature) {
    const enabledFeatures = {
      chatStreaming: false, // Enable when API supports streaming
      fileUpload: true,
      reports: true,
      ttv: false // Coming soon
    };

    return enabledFeatures[feature] ?? false;
  }
};

// Add toast animations to document
const style = document.createElement('style');
style.textContent = `
  @keyframes toastIn {
    from {
      opacity: 0;
      transform: translateX(-50%) translateY(20px);
    }
    to {
      opacity: 1;
      transform: translateX(-50%) translateY(0);
    }
  }

  @keyframes toastOut {
    from {
      opacity: 1;
      transform: translateX(-50%) translateY(0);
    }
    to {
      opacity: 0;
      transform: translateX(-50%) translateY(20px);
    }
  }
`;
document.head.appendChild(style);

// Initialize app on DOM ready
document.addEventListener('DOMContentLoaded', () => {
  App.init();
});
