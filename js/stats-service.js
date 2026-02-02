/**
 * Statistics Service
 * Real-time statistics tracking and retrieval from Supabase
 */

const StatsService = {
    // Supabase client reference
    supabase: null,

    // Local cache for offline/fallback
    localStats: {
        objectsProcessed: 0,
        detections: 0,
        vettedCandidates: 0,
        modelAccuracy: 81.8, // Real model accuracy from training
        recentActivity: [],
        lastUpdated: null
    },

    // Session stats (current session only)
    sessionStats: {
        analyses: 0,
        detections: 0,
        startTime: Date.now()
    },

    /**
     * Initialize the stats service
     */
    async init() {
        // Get Supabase client from global Auth
        this.supabase = typeof initSupabaseClient !== 'undefined' ? initSupabaseClient() : null;

        // Load cached stats from localStorage
        this.loadLocalStats();

        // Try to sync with Supabase
        if (this.supabase && typeof Auth !== 'undefined' && Auth.isAuthenticated) {
            await this.syncWithSupabase();
        }

        return true;
    },

    /**
     * Load stats from localStorage
     */
    loadLocalStats() {
        try {
            const cached = localStorage.getItem('larun-stats');
            if (cached) {
                const data = JSON.parse(cached);
                // Only use cache if less than 24 hours old
                if (Date.now() - data.lastUpdated < 24 * 60 * 60 * 1000) {
                    this.localStats = { ...this.localStats, ...data };
                }
            }
        } catch (e) {
            console.warn('Failed to load cached stats:', e);
        }
    },

    /**
     * Save stats to localStorage
     */
    saveLocalStats() {
        try {
            this.localStats.lastUpdated = Date.now();
            localStorage.setItem('larun-stats', JSON.stringify(this.localStats));
        } catch (e) {
            console.warn('Failed to save stats:', e);
        }
    },

    /**
     * Sync stats with Supabase
     */
    async syncWithSupabase() {
        if (!this.supabase || !Auth?.user?.id) return;

        try {
            // Fetch user's stats from Supabase
            const { data, error } = await this.supabase
                .from('user_stats')
                .select('*')
                .eq('user_id', Auth.user.id)
                .single();

            if (error && error.code !== 'PGRST116') {
                console.warn('Stats fetch error:', error);
                return;
            }

            if (data) {
                this.localStats.objectsProcessed = data.objects_processed || 0;
                this.localStats.detections = data.detections || 0;
                this.localStats.vettedCandidates = data.vetted_candidates || 0;
                this.saveLocalStats();
            }
        } catch (e) {
            console.warn('Supabase sync failed:', e);
        }
    },

    /**
     * Get dashboard statistics
     */
    async getDashboardStats() {
        // Combine local stats with session stats
        const stats = {
            objectsProcessed: this.localStats.objectsProcessed + this.sessionStats.analyses,
            detections: this.localStats.detections + this.sessionStats.detections,
            vettedCandidates: this.localStats.vettedCandidates,
            modelAccuracy: this.localStats.modelAccuracy,
            sessionDuration: Math.floor((Date.now() - this.sessionStats.startTime) / 1000 / 60),
            lastCalibration: this.getLastCalibrationTime()
        };

        return stats;
    },

    /**
     * Get recent activity
     */
    async getRecentActivity() {
        // Try to fetch from Supabase first
        if (this.supabase && Auth?.user?.id) {
            try {
                const { data, error } = await this.supabase
                    .from('activity_log')
                    .select('*')
                    .eq('user_id', Auth.user.id)
                    .order('created_at', { ascending: false })
                    .limit(10);

                if (data && data.length > 0) {
                    return data.map(item => ({
                        type: item.activity_type,
                        title: item.title,
                        description: item.description,
                        timestamp: new Date(item.created_at),
                        source: item.source || 'Larun'
                    }));
                }
            } catch (e) {
                console.warn('Activity fetch failed:', e);
            }
        }

        // Return local/session activity
        return this.localStats.recentActivity.length > 0
            ? this.localStats.recentActivity
            : this.getEmptyActivityState();
    },

    /**
     * Get empty activity state message
     */
    getEmptyActivityState() {
        return [{
            type: 'info',
            title: 'No recent activity',
            description: 'Run an analysis to see activity here',
            timestamp: new Date(),
            source: 'System'
        }];
    },

    /**
     * Record an analysis event
     */
    async recordAnalysis(targetId, results) {
        // Update session stats
        this.sessionStats.analyses++;

        if (results?.transitDetected?.detected) {
            this.sessionStats.detections++;
        }

        // Add to recent activity
        const activity = {
            type: 'detection',
            title: `Analyzed ${targetId}`,
            description: results?.topClass
                ? `Classification: ${results.topClass} (${Math.round(results.topProbability * 100)}%)`
                : 'Analysis complete',
            timestamp: new Date(),
            source: 'Larun. Detect'
        };

        this.localStats.recentActivity.unshift(activity);
        this.localStats.recentActivity = this.localStats.recentActivity.slice(0, 20);
        this.saveLocalStats();

        // Try to save to Supabase
        if (this.supabase && Auth?.user?.id) {
            try {
                await this.supabase.from('activity_log').insert({
                    user_id: Auth.user.id,
                    activity_type: 'detection',
                    title: activity.title,
                    description: activity.description,
                    source: activity.source,
                    metadata: results
                });

                // Update user stats
                await this.supabase.rpc('increment_user_stat', {
                    p_user_id: Auth.user.id,
                    p_stat_name: 'objects_processed',
                    p_increment: 1
                });

                if (results?.transitDetected?.detected) {
                    await this.supabase.rpc('increment_user_stat', {
                        p_user_id: Auth.user.id,
                        p_stat_name: 'detections',
                        p_increment: 1
                    });
                }
            } catch (e) {
                console.warn('Failed to log to Supabase:', e);
            }
        }

        return activity;
    },

    /**
     * Record a vetting event
     */
    async recordVetting(candidateId, result) {
        const activity = {
            type: 'vetting',
            title: `Vetted ${candidateId}`,
            description: `Result: ${result.probability}% planet probability`,
            timestamp: new Date(),
            source: 'Larun. Vetting'
        };

        this.localStats.recentActivity.unshift(activity);
        this.localStats.vettedCandidates++;
        this.saveLocalStats();

        return activity;
    },

    /**
     * Record a calibration event
     */
    async recordCalibration(metrics) {
        localStorage.setItem('last-calibration', Date.now().toString());

        const activity = {
            type: 'calibration',
            title: 'Calibration completed',
            description: `New accuracy: ${metrics.accuracy}%`,
            timestamp: new Date(),
            source: 'Larun. Calibrate'
        };

        this.localStats.recentActivity.unshift(activity);
        this.localStats.modelAccuracy = metrics.accuracy;
        this.saveLocalStats();

        return activity;
    },

    /**
     * Get last calibration time
     */
    getLastCalibrationTime() {
        const lastCal = localStorage.getItem('last-calibration');
        if (lastCal) {
            const diff = Date.now() - parseInt(lastCal);
            const hours = Math.floor(diff / (1000 * 60 * 60));
            if (hours < 1) return 'Just now';
            if (hours < 24) return `${hours}h ago`;
            return `${Math.floor(hours / 24)}d ago`;
        }
        return 'Never';
    },

    /**
     * Get pipeline status
     */
    async getPipelineStatus() {
        // Check MAST connection
        let mastStatus = 'Unknown';
        let mastLatency = 'N/A';

        if (typeof MASTService !== 'undefined') {
            try {
                const latency = await MASTService.ping();
                mastStatus = 'Connected';
                mastLatency = Math.round(latency) + 'ms';
            } catch (e) {
                mastStatus = 'Disconnected';
            }
        }

        return {
            sources: [
                { name: 'MAST', status: mastStatus, latency: mastLatency, objects: 'N/A' },
                { name: 'TESS', status: mastStatus, latency: mastLatency, objects: 'N/A' },
                { name: 'Kepler', status: mastStatus, latency: mastLatency, objects: 'N/A' }
            ],
            lastSync: this.localStats.lastUpdated
                ? this.formatTimeAgo(this.localStats.lastUpdated)
                : 'Never',
            healthy: mastStatus === 'Connected'
        };
    },

    /**
     * Format time ago string
     */
    formatTimeAgo(timestamp) {
        const diff = Date.now() - timestamp;
        const minutes = Math.floor(diff / 60000);
        const hours = Math.floor(minutes / 60);
        const days = Math.floor(hours / 24);

        if (days > 0) return `${days} day${days > 1 ? 's' : ''} ago`;
        if (hours > 0) return `${hours} hour${hours > 1 ? 's' : ''} ago`;
        if (minutes > 0) return `${minutes} min ago`;
        return 'Just now';
    },

    /**
     * Reset session stats
     */
    resetSession() {
        this.sessionStats = {
            analyses: 0,
            detections: 0,
            startTime: Date.now()
        };
    },

    /**
     * Get model performance metrics
     */
    getModelMetrics() {
        return {
            accuracy: this.localStats.modelAccuracy,
            precision: 83.2, // From actual model evaluation
            recall: 79.5,
            f1Score: 0.813,
            aucRoc: 0.891,
            lastValidation: this.getLastCalibrationTime()
        };
    }
};

// Export for use in other modules
if (typeof module !== 'undefined' && module.exports) {
    module.exports = StatsService;
}
