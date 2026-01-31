/**
 * LARUN.SPACE - API Client
 * Handles communication with the LARUN backend API
 */

const LarunAPI = {
  // Configuration
  baseURL: 'http://localhost:8000', // Update for production
  version: 'v1',

  // Get full API URL
  getURL(endpoint) {
    return `${this.baseURL}/${this.version}${endpoint}`;
  },

  // Get auth headers
  getHeaders() {
    const headers = {
      'Content-Type': 'application/json'
    };

    const token = localStorage.getItem('larun_token');
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    return headers;
  },

  // Generic API request
  async request(method, endpoint, data = null) {
    const options = {
      method,
      headers: this.getHeaders()
    };

    if (data && method !== 'GET') {
      options.body = JSON.stringify(data);
    }

    try {
      const response = await fetch(this.getURL(endpoint), options);

      if (!response.ok) {
        const error = await response.json().catch(() => ({}));
        throw new Error(error.detail || `API Error: ${response.status}`);
      }

      return await response.json();
    } catch (error) {
      console.error('API Error:', error);
      throw error;
    }
  },

  // ============================================
  // Analysis Endpoints
  // ============================================

  /**
   * Run BLS periodogram analysis
   * @param {string} ticId - TIC identifier
   * @param {object} options - Additional options
   */
  async analyzeBLS(ticId, options = {}) {
    return this.request('POST', '/analyze/bls', {
      tic_id: ticId,
      period_min: options.periodMin || 0.5,
      period_max: options.periodMax || 15.0,
      ...options
    });
  },

  /**
   * Fit transit model parameters
   * @param {string} ticId - TIC identifier
   * @param {number} period - Orbital period in days
   * @param {number} t0 - Transit mid-time (BJD)
   */
  async fitTransit(ticId, period, t0, options = {}) {
    return this.request('POST', '/analyze/fit', {
      tic_id: ticId,
      period,
      t0,
      ...options
    });
  },

  /**
   * Run TinyML transit detection
   * @param {string} ticId - TIC identifier
   */
  async detectTransit(ticId) {
    return this.request('POST', '/analyze/detect', {
      tic_id: ticId
    });
  },

  // ============================================
  // Stellar Endpoints
  // ============================================

  /**
   * Classify host star
   * @param {string} ticId - TIC identifier
   */
  async classifyStar(ticId) {
    return this.request('POST', '/stellar/classify', {
      tic_id: ticId
    });
  },

  /**
   * Get stellar parameters
   * @param {string} ticId - TIC identifier
   */
  async getStellarParams(ticId) {
    return this.request('GET', `/stellar/${ticId}`);
  },

  // ============================================
  // Planet Endpoints
  // ============================================

  /**
   * Estimate planet radius
   * @param {object} params - Transit and stellar parameters
   */
  async estimatePlanetRadius(params) {
    return this.request('POST', '/planet/radius', params);
  },

  /**
   * Check habitable zone
   * @param {object} params - Stellar and orbital parameters
   */
  async checkHabitableZone(params) {
    return this.request('POST', '/planet/hz', params);
  },

  // ============================================
  // Pipeline Endpoints
  // ============================================

  /**
   * Run full analysis pipeline
   * @param {string} ticId - TIC identifier
   * @param {object} options - Pipeline options
   */
  async runPipeline(ticId, options = {}) {
    return this.request('POST', '/pipeline', {
      tic_id: ticId,
      full_report: options.fullReport || false,
      ...options
    });
  },

  /**
   * Generate analysis report
   * @param {string} analysisId - Previous analysis ID
   */
  async generateReport(analysisId) {
    return this.request('POST', '/pipeline/report', {
      analysis_id: analysisId
    });
  },

  // ============================================
  // Data Endpoints
  // ============================================

  /**
   * Fetch light curve data
   * @param {string} ticId - TIC identifier
   * @param {string} mission - Mission (tess, kepler)
   */
  async getLightCurve(ticId, mission = 'tess') {
    return this.request('GET', `/data/lightcurve/${ticId}?mission=${mission}`);
  },

  /**
   * Upload light curve file
   * @param {File} file - Light curve file (FITS, CSV, TXT)
   */
  async uploadLightCurve(file) {
    const formData = new FormData();
    formData.append('file', file);

    const token = localStorage.getItem('larun_token');
    const headers = {};
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(this.getURL('/data/upload'), {
      method: 'POST',
      headers,
      body: formData
    });

    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(error.detail || `Upload Error: ${response.status}`);
    }

    return await response.json();
  },

  // ============================================
  // Chat Endpoints
  // ============================================

  /**
   * Send chat message for AI processing
   * @param {string} message - User message
   * @param {string} conversationId - Conversation ID
   */
  async chat(message, conversationId = null) {
    return this.request('POST', '/chat', {
      message,
      conversation_id: conversationId
    });
  },

  /**
   * Stream chat response (for real-time updates)
   * @param {string} message - User message
   * @param {function} onChunk - Callback for each chunk
   */
  async chatStream(message, conversationId, onChunk) {
    const response = await fetch(this.getURL('/chat/stream'), {
      method: 'POST',
      headers: this.getHeaders(),
      body: JSON.stringify({
        message,
        conversation_id: conversationId
      })
    });

    const reader = response.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;

      const chunk = decoder.decode(value);
      const lines = chunk.split('\n').filter(line => line.trim());

      for (const line of lines) {
        if (line.startsWith('data: ')) {
          try {
            const data = JSON.parse(line.slice(6));
            onChunk(data);
          } catch (e) {
            // Not JSON, likely text content
            onChunk({ type: 'text', content: line.slice(6) });
          }
        }
      }
    }
  },

  // ============================================
  // User Endpoints
  // ============================================

  /**
   * Get current user info
   */
  async getCurrentUser() {
    return this.request('GET', '/user/me');
  },

  /**
   * Get usage stats
   */
  async getUsageStats() {
    return this.request('GET', '/user/usage');
  }
};

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
  module.exports = LarunAPI;
}
