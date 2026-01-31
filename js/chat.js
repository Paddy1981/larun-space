/**
 * LARUN.SPACE - Chat Interface
 * Handles chat functionality and message management
 */

const Chat = {
  // State
  conversations: [],
  currentConversation: null,
  messages: [],
  isLoading: false,

  // DOM Elements
  elements: {
    messagesContainer: null,
    messagesList: null,
    welcomeScreen: null,
    typingIndicator: null,
    messageInput: null,
    sendBtn: null
  },

  // Initialize chat
  init() {
    this.cacheElements();
    this.loadConversations();
    this.setupEventListeners();
    this.updateUI();
  },

  // Cache DOM elements
  cacheElements() {
    this.elements.messagesContainer = document.getElementById('messages-container');
    this.elements.messagesList = document.getElementById('messages-list');
    this.elements.welcomeScreen = document.getElementById('welcome-screen');
    this.elements.typingIndicator = document.getElementById('typing-indicator');
    this.elements.messageInput = document.getElementById('message-input');
    this.elements.sendBtn = document.getElementById('send-btn');
  },

  // Setup event listeners
  setupEventListeners() {
    // Input changes
    if (this.elements.messageInput) {
      this.elements.messageInput.addEventListener('input', () => {
        this.updateSendButton();
      });
    }
  },

  // ============================================
  // Conversation Management
  // ============================================

  // Load conversations from localStorage
  loadConversations() {
    try {
      const saved = localStorage.getItem('larun_conversations');
      this.conversations = saved ? JSON.parse(saved) : [];
      this.renderConversationList();
    } catch (e) {
      console.error('Failed to load conversations:', e);
      this.conversations = [];
    }
  },

  // Save conversations to localStorage
  saveConversations() {
    try {
      localStorage.setItem('larun_conversations', JSON.stringify(this.conversations));
    } catch (e) {
      console.error('Failed to save conversations:', e);
    }
  },

  // Create new conversation
  createConversation(title = 'New Chat') {
    const conversation = {
      id: Date.now().toString(),
      title,
      messages: [],
      createdAt: new Date().toISOString(),
      updatedAt: new Date().toISOString()
    };

    this.conversations.unshift(conversation);
    this.saveConversations();
    this.selectConversation(conversation.id);

    return conversation;
  },

  // Select a conversation
  selectConversation(id) {
    const conversation = this.conversations.find(c => c.id === id);
    if (!conversation) return;

    this.currentConversation = conversation;
    this.messages = conversation.messages;
    this.renderMessages();
    this.renderConversationList();
  },

  // Update conversation title based on first message
  updateConversationTitle(conversation, message) {
    // Extract a short title from the message
    const title = message.slice(0, 40) + (message.length > 40 ? '...' : '');
    conversation.title = title;
    this.saveConversations();
    this.renderConversationList();
  },

  // Delete conversation
  deleteConversation(id) {
    this.conversations = this.conversations.filter(c => c.id !== id);
    this.saveConversations();

    if (this.currentConversation?.id === id) {
      this.currentConversation = null;
      this.messages = [];
      this.updateUI();
    }

    this.renderConversationList();
  },

  // ============================================
  // Message Handling
  // ============================================

  // Send a message
  async sendMessage(text = null) {
    const message = text || this.elements.messageInput?.value.trim();
    if (!message || this.isLoading) return;

    // Create conversation if needed
    if (!this.currentConversation) {
      this.createConversation();
      this.updateConversationTitle(this.currentConversation, message);
    }

    // Clear input
    if (this.elements.messageInput) {
      this.elements.messageInput.value = '';
      this.updateSendButton();
      autoResizeInput(this.elements.messageInput);
    }

    // Add user message
    this.addMessage('user', message);

    // Show typing indicator
    this.setLoading(true);

    try {
      // Send to API
      const response = await this.processMessage(message);

      // Add assistant response
      this.addMessage('assistant', response);
    } catch (error) {
      console.error('Error sending message:', error);
      this.addMessage('assistant', `Sorry, I encountered an error: ${error.message}. Please try again.`);
    } finally {
      this.setLoading(false);
    }
  },

  // Process message (AI response simulation or API call)
  async processMessage(message) {
    // Try to use API if available
    if (typeof LarunAPI !== 'undefined') {
      try {
        const response = await LarunAPI.chat(message, this.currentConversation?.id);
        return response.response || response.message || this.getSimulatedResponse(message);
      } catch (e) {
        console.log('API not available, using simulated response');
        // Fall through to simulated response
      }
    }

    // Simulated response for demo
    return this.getSimulatedResponse(message);
  },

  // Get simulated AI response
  getSimulatedResponse(message) {
    const lowerMessage = message.toLowerCase();

    // TIC/Target search
    if (lowerMessage.includes('tic') || lowerMessage.includes('search') || lowerMessage.includes('transit')) {
      const ticMatch = message.match(/TIC\s*(\d+)/i) || message.match(/(\d{6,})/);
      const ticId = ticMatch ? ticMatch[1] : '307210830';

      return `I'll analyze TIC ${ticId} for transit signals.

**Fetching Data**
- Mission: TESS
- Sectors: 1, 2, 3

**BLS Periodogram Results**
| Parameter | Value |
|-----------|-------|
| Period | 3.425 ± 0.001 days |
| T₀ (BJD) | 2458765.432 |
| Depth | 2,300 ± 120 ppm |
| Duration | 2.5 hours |
| SNR | 12.4 |

**TinyML Detection**
✓ Transit candidate detected with 87.3% confidence

The light curve shows a clear periodic signal consistent with a planetary transit. Would you like me to:
1. Fit the transit model for detailed parameters?
2. Check if this planet is in the habitable zone?
3. Generate a full analysis report?`;
    }

    // Habitable zone
    if (lowerMessage.includes('habitable') || lowerMessage.includes('hz')) {
      return `**Habitable Zone Analysis**

Based on the stellar parameters:
- Stellar Teff: 3,480 K (M dwarf)
- Stellar Luminosity: 0.023 L☉
- Planet Semi-major axis: 0.163 AU

**Result: ✓ Within the Habitable Zone**

The planet receives approximately 86% of Earth's insolation, placing it in the conservative habitable zone where liquid water could exist on the surface.

**Equilibrium Temperature**
- Assuming Earth-like albedo (0.3): 255 K (-18°C)
- With greenhouse effect: ~288 K (15°C)

This is an excellent candidate for atmospheric characterization with JWST.`;
    }

    // Kepler
    if (lowerMessage.includes('kepler')) {
      const keplerMatch = message.match(/Kepler-(\d+)/i);
      const keplerId = keplerMatch ? keplerMatch[1] : '11';

      return `**Kepler-${keplerId} System Analysis**

Kepler-${keplerId} is a fascinating multi-planet system. Here's what we know:

| Parameter | Value |
|-----------|-------|
| Host Star | G-type (solar-like) |
| Distance | 2,000 light years |
| Planets | 6 confirmed |

**Light Curve**
The system shows complex transit patterns due to multiple planets. I can:
1. Analyze individual planet transits
2. Search for Transit Timing Variations (TTVs)
3. Look for additional candidates

What would you like to explore?`;
    }

    // Report generation
    if (lowerMessage.includes('report') || lowerMessage.includes('generate')) {
      return `**Generating Analysis Report**

I'm preparing a comprehensive report including:

1. **Target Summary**
   - Stellar parameters
   - Observation metadata

2. **Detection Results**
   - BLS periodogram
   - TinyML classification
   - Signal-to-noise analysis

3. **Planet Characterization**
   - Orbital parameters
   - Radius estimate
   - Habitability assessment

4. **Figures**
   - Light curve with transits marked
   - Folded light curve
   - Periodogram

📄 [Download Report (PDF)](#)

The report follows TESS Follow-up Observing Program (TFOP) guidelines and can be used for publication or follow-up proposals.`;
    }

    // Default response
    return `I understand you're asking about: "${message}"

I can help you with:
- **Transit Search**: "Search for transits in TIC 307210830"
- **Light Curve Analysis**: "Analyze light curve for Kepler-11"
- **Habitability Check**: "Is TOI-700 d in the habitable zone?"
- **Report Generation**: "Generate a report for my candidate"

What would you like to explore?`;
  },

  // Add a message to the conversation
  addMessage(role, content) {
    const message = {
      id: Date.now().toString(),
      role,
      content,
      timestamp: new Date().toISOString()
    };

    this.messages.push(message);

    if (this.currentConversation) {
      this.currentConversation.messages = this.messages;
      this.currentConversation.updatedAt = message.timestamp;
      this.saveConversations();
    }

    this.renderMessage(message);
    this.scrollToBottom();
  },

  // ============================================
  // Rendering
  // ============================================

  // Render all messages
  renderMessages() {
    if (!this.elements.messagesList) return;

    this.elements.messagesList.innerHTML = '';

    if (this.messages.length === 0) {
      this.showWelcomeScreen();
      return;
    }

    this.hideWelcomeScreen();

    for (const message of this.messages) {
      this.renderMessage(message);
    }

    this.scrollToBottom();
  },

  // Render a single message
  renderMessage(message) {
    if (!this.elements.messagesList) return;

    this.hideWelcomeScreen();

    const messageEl = document.createElement('div');
    messageEl.className = 'message';
    messageEl.dataset.id = message.id;

    const isUser = message.role === 'user';

    messageEl.innerHTML = `
      <div class="message-avatar ${message.role}">
        ${isUser ? this.getUserInitial() : `
          <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
            <path d="M12 2L15.09 8.26L22 9.27L17 14.14L18.18 21.02L12 17.77L5.82 21.02L7 14.14L2 9.27L8.91 8.26L12 2Z"/>
          </svg>
        `}
      </div>
      <div class="message-content">
        <div class="message-sender">${isUser ? 'You' : 'LARUN'}</div>
        <div class="message-text">${this.formatMessage(message.content)}</div>
      </div>
    `;

    this.elements.messagesList.appendChild(messageEl);
  },

  // Format message content (markdown-like)
  formatMessage(content) {
    // Escape HTML
    let formatted = content
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;');

    // Bold
    formatted = formatted.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');

    // Inline code
    formatted = formatted.replace(/`([^`]+)`/g, '<code>$1</code>');

    // Tables
    if (formatted.includes('|')) {
      formatted = this.formatTable(formatted);
    }

    // Line breaks
    formatted = formatted.replace(/\n/g, '<br>');

    // Links
    formatted = formatted.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" style="color: var(--cosmic-purple-light);">$1</a>');

    return formatted;
  },

  // Format markdown tables
  formatTable(content) {
    const lines = content.split('\n');
    let inTable = false;
    let tableHtml = '';
    let result = [];

    for (const line of lines) {
      if (line.includes('|') && !line.match(/^\s*\|?\s*[-:]+\s*\|/)) {
        if (!inTable) {
          inTable = true;
          tableHtml = '<table class="data-table"><tbody>';
        }

        const cells = line.split('|').filter(c => c.trim());
        const isHeader = !tableHtml.includes('<tr>');

        if (isHeader) {
          tableHtml += '<thead><tr>';
          for (const cell of cells) {
            tableHtml += `<th>${cell.trim()}</th>`;
          }
          tableHtml += '</tr></thead><tbody>';
        } else {
          tableHtml += '<tr>';
          for (const cell of cells) {
            tableHtml += `<td>${cell.trim()}</td>`;
          }
          tableHtml += '</tr>';
        }
      } else if (line.match(/^\s*\|?\s*[-:]+\s*\|/)) {
        // Table separator line, skip
        continue;
      } else {
        if (inTable) {
          tableHtml += '</tbody></table>';
          result.push(tableHtml);
          tableHtml = '';
          inTable = false;
        }
        result.push(line);
      }
    }

    if (inTable) {
      tableHtml += '</tbody></table>';
      result.push(tableHtml);
    }

    return result.join('\n');
  },

  // Get user initial for avatar
  getUserInitial() {
    const user = JSON.parse(localStorage.getItem('larun_user') || '{}');
    return (user.name?.[0] || user.email?.[0] || '?').toUpperCase();
  },

  // Render conversation list in sidebar
  renderConversationList() {
    const today = new Date();
    const yesterday = new Date(today);
    yesterday.setDate(yesterday.getDate() - 1);
    const weekAgo = new Date(today);
    weekAgo.setDate(weekAgo.getDate() - 7);

    const todayList = document.getElementById('today-conversations');
    const yesterdayList = document.getElementById('yesterday-conversations');
    const weekList = document.getElementById('week-conversations');

    if (todayList) todayList.innerHTML = '';
    if (yesterdayList) yesterdayList.innerHTML = '';
    if (weekList) weekList.innerHTML = '';

    for (const conv of this.conversations) {
      const date = new Date(conv.updatedAt);
      let targetList = weekList;

      if (date.toDateString() === today.toDateString()) {
        targetList = todayList;
      } else if (date.toDateString() === yesterday.toDateString()) {
        targetList = yesterdayList;
      }

      if (targetList) {
        const item = document.createElement('div');
        item.className = 'conversation-item' + (this.currentConversation?.id === conv.id ? ' active' : '');
        item.onclick = () => this.selectConversation(conv.id);
        item.innerHTML = `
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>
          </svg>
          <span class="conversation-title">${conv.title}</span>
        `;
        targetList.appendChild(item);
      }
    }
  },

  // Show welcome screen
  showWelcomeScreen() {
    if (this.elements.welcomeScreen) {
      this.elements.welcomeScreen.classList.remove('hidden');
    }
  },

  // Hide welcome screen
  hideWelcomeScreen() {
    if (this.elements.welcomeScreen) {
      this.elements.welcomeScreen.classList.add('hidden');
    }
  },

  // Set loading state
  setLoading(loading) {
    this.isLoading = loading;

    if (this.elements.typingIndicator) {
      this.elements.typingIndicator.classList.toggle('hidden', !loading);
    }

    this.updateSendButton();
    this.scrollToBottom();
  },

  // Update send button state
  updateSendButton() {
    if (!this.elements.sendBtn || !this.elements.messageInput) return;

    const hasText = this.elements.messageInput.value.trim().length > 0;
    this.elements.sendBtn.disabled = !hasText || this.isLoading;
  },

  // Scroll to bottom of messages
  scrollToBottom() {
    if (this.elements.messagesContainer) {
      this.elements.messagesContainer.scrollTop = this.elements.messagesContainer.scrollHeight;
    }
  },

  // Update UI state
  updateUI() {
    if (this.messages.length > 0) {
      this.hideWelcomeScreen();
      this.renderMessages();
    } else {
      this.showWelcomeScreen();
    }
    this.renderConversationList();
    this.updateSendButton();
  }
};

// ============================================
// Global Functions (called from HTML)
// ============================================

function startNewChat() {
  Chat.currentConversation = null;
  Chat.messages = [];
  Chat.updateUI();
  closeSidebar();
}

function sendMessage() {
  Chat.sendMessage();
}

function useSuggestedPrompt(prompt) {
  const input = document.getElementById('message-input');
  if (input) {
    input.value = prompt;
    input.focus();
    Chat.updateSendButton();
    autoResizeInput(input);
  }
}

function handleInputKeydown(event) {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    sendMessage();
  }
}

function autoResizeInput(textarea) {
  textarea.style.height = 'auto';
  textarea.style.height = Math.min(textarea.scrollHeight, 200) + 'px';
}

function openFileUpload() {
  document.getElementById('upload-modal')?.classList.add('active');
}

function closeUploadModal() {
  document.getElementById('upload-modal')?.classList.remove('active');
}

async function handleFileUpload(event) {
  const file = event.target.files[0];
  if (!file) return;

  closeUploadModal();

  // Show message that file is being processed
  Chat.addMessage('user', `Uploading file: ${file.name}`);
  Chat.setLoading(true);

  try {
    if (typeof LarunAPI !== 'undefined') {
      const result = await LarunAPI.uploadLightCurve(file);
      Chat.addMessage('assistant', `File uploaded successfully. I found ${result.points || 'many'} data points. What would you like me to analyze?`);
    } else {
      // Simulated response
      Chat.addMessage('assistant', `I've received your light curve file (${file.name}). The data contains approximately 50,000 data points spanning 27 days. Would you like me to search for transit signals?`);
    }
  } catch (error) {
    Chat.addMessage('assistant', `Sorry, I couldn't process the file: ${error.message}`);
  } finally {
    Chat.setLoading(false);
  }
}

// Sidebar toggle functions
function toggleSidebar() {
  document.getElementById('sidebar')?.classList.toggle('open');
  document.getElementById('sidebar-overlay')?.classList.toggle('active');
}

function closeSidebar() {
  document.getElementById('sidebar')?.classList.remove('open');
  document.getElementById('sidebar-overlay')?.classList.remove('active');
}

// Initialize on DOM ready
document.addEventListener('DOMContentLoaded', () => {
  Chat.init();
});
